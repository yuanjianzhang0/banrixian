# -*- coding: utf-8 -*-
"""Order routes."""

from __future__ import annotations

import json
import time
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Body, Depends

import pymysql

from core.auth import get_current_user
from core.database import get_db
from skills.sms import send_sms


logger = logging.getLogger("BanrixianAPI")

router = APIRouter(prefix="/v1/orders", tags=["orders"])


def _find_existing_order(cursor, user_id: str, title: str, date_str: str) -> str:
    cursor.execute(
        "SELECT id FROM orders WHERE user_id = %s AND title = %s AND date_str = %s LIMIT 1",
        (user_id, title, date_str),
    )
    row = cursor.fetchone()
    return str(row.get("id") or "") if row else ""


@router.get("")
async def get_orders_list(status: str = "all", city: str = "all", current_user: dict = Depends(get_current_user)):
    """Get user orders."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            clauses = ["user_id = %s"]
            args = [current_user["id"]]
            if status != "all":
                clauses.append("status = %s")
                args.append(status)
            if city and city != "all":
                # 精确匹配城市；city IS NULL 的旧数据在全部模式下可见，不归入单城市
                clauses.append("city = %s")
                args.append(city)
            cursor.execute(
                "SELECT *, COALESCE(city, '') AS city, COALESCE(created_at, date_str) AS sort_key "
                f"FROM orders WHERE {' AND '.join(clauses)} ORDER BY id DESC",
                tuple(args),
            )
            rows = cursor.fetchall()
            data = []
            for r in rows:
                try:
                    r["tags"] = json.loads(r["tags"]) if r.get("tags") else []
                except Exception:
                    r["tags"] = []
                r["desc"] = r.pop("desc_text", "")
                # 序列化 datetime
                for k in ("created_at", "sort_key"):
                    if r.get(k) is not None:
                        r[k] = str(r[k])
                data.append(r)
            return {"code": 200, "data": data}
    except pymysql.Error as e:
        logger.error(f"查询订单时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="查询失败")
    finally:
        conn.close()


@router.post("")
async def create_order(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Create a new order."""
    conn = get_db()
    try:
        service_id = payload.get("serviceId")
        option = payload.get("option", {})
        order_type = payload.get("type")
        
        current_date = datetime.now().strftime("%Y-%m-%d")
        order_id = str(int(time.time() * 1000))
        city = (payload.get("city") or current_user.get("city") or "北京").strip() or "北京"
        
        with conn.cursor() as cursor:
            if order_type == "plan":
                order = {
                    "id": order_id,
                    "ico": "🗓️",
                    "title": payload.get("title") or "智能行程组合预订",
                    "desc": payload.get("desc") or "基于AI为您量身打造的即兴行程",
                    "date": current_date,
                    "status": "ongoing",
                    "statusText": "进行中",
                    "price": payload.get("price") or "待结算",
                    "tags": ["AI规划"]
                }
            else:
                cursor.execute("SELECT * FROM services WHERE id = %s", (service_id or 1,))
                svc_row = cursor.fetchone()
                svc = svc_row if svc_row else {"ico": "📦", "name": "本地全服务"}
                order = {
                    "id": order_id,
                    "ico": svc["ico"],
                    "title": svc["name"],
                    "desc": f"{option.get('name', '标准套餐')} · 预订成功",
                    "date": current_date,
                    "status": "ongoing",
                    "statusText": "进行中",
                    "price": f"¥{option.get('price', '98')}",
                    "tags": ["单项服务"]
                }
            
            cursor.execute(
                "INSERT INTO orders (id, ico, title, desc_text, date_str, status, statusText, price, tags, user_id, city) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (order["id"], order["ico"], order["title"], order["desc"], order["date"], order["status"], order["statusText"], order["price"], json.dumps(order["tags"]), current_user["id"], city)
            )
        conn.commit()
        logger.info(f"🛒 创建新订单成功: 订单号 {order_id}, 用户 {current_user['name']}")
        return {"code": 200, "message": "success", "data": order}
    except pymysql.Error as e:
        logger.error(f"创建订单时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="创建失败")
    finally:
        conn.close()


@router.post("/confirm-reservations")
async def confirm_reservations(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """用户从 AI 规划结果中选择要预约的地点，每个地点写一条订单。

    payload 示例:
    {
        "plan_summary": "今天下午亲子出行",
        "reservations": [
            {"place_name": "极光亲子探险乐园", "time": "14:00", "people_count": 4, "price": "¥472"},
            {"place_name": "拾味海鲜蒸汽锅",   "time": "16:30", "people_count": 4, "price": "¥352"}
        ]
    }
    """
    reservations = payload.get("reservations") or []
    if not reservations:
        raise HTTPException(status_code=400, detail="reservations 不能为空")

    plan_summary = (payload.get("plan_summary") or "AI规划行程").strip()
    city = (payload.get("city") or current_user.get("city") or "北京").strip() or "北京"
    today_date = datetime.now().strftime("%Y-%m-%d")
    created = []

    conn = get_db()
    try:
        with conn.cursor() as cursor:
            for item in reservations:
                place_name = (item.get("place_name") or "").strip()[:128]
                if not place_name:
                    continue
                reserve_time = (item.get("time") or "").strip()[:20]
                # 优先使用路线里的 date 字段，没有则用今天
                reserve_date = (item.get("date") or "").strip() or today_date
                people_count = item.get("people_count") or 1
                price = (item.get("price") or "").strip()[:120]  # 防止超长价格字段导致 INSERT 失败
                existing_id = _find_existing_order(cursor, current_user["id"], place_name, reserve_date)
                if existing_id:
                    created.append({
                        "id": existing_id,
                        "place_name": place_name,
                        "time": reserve_time,
                        "date": reserve_date,
                        "people_count": people_count,
                        "price": price or "待结算",
                        "city": (item.get("city") or city).strip() or "北京",
                        "status": "duplicate_skipped",
                        "message": "该地点已在行程中，已避免重复预约。",
                    })
                    continue

                order_id = str(int(time.time() * 1000)) + str(len(created))
                date_prefix = f"{reserve_date} " if reserve_date != today_date else ""
                desc = f"{date_prefix}{reserve_time} · {people_count}人" if reserve_time else f"{people_count}人"
                item_city = (item.get("city") or city).strip() or "北京"
                cursor.execute(
                    "INSERT INTO orders (id, ico, title, desc_text, date_str, status, statusText, price, tags, user_id, plan_summary, city, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                    (
                        order_id, "📍", place_name, desc,
                        reserve_date, "ongoing", "进行中",
                        price or "待结算",
                        json.dumps(["AI规划", "预约"]),
                        current_user["id"],
                        plan_summary,
                        item_city,
                    ),
                )
                created.append({
                    "id": order_id,
                    "place_name": place_name,
                    "time": reserve_time,
                    "date": reserve_date,
                    "people_count": people_count,
                    "price": price or "待结算",
                    "city": item_city,
                    "status": "ongoing",
                })
        conn.commit()
        logger.info(f"✅ 用户 {current_user['name']} 确认预约 {len(created)} 个地点（{plan_summary}）")
        return {"code": 200, "message": "预约成功", "data": {"orders": created}}
    except pymysql.Error as e:
        logger.error(f"确认预约时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="预约失败")
    finally:
        conn.close()


@router.post("/execute-actions")
async def execute_action_bundle(payload: dict = Body(...), current_user: dict = Depends(get_current_user)):
    """Execute a user-confirmed AI action bundle.

    This is a mock execution layer for the hackathon demo: reservations are
    persisted as itinerary orders, while notify/share/gift actions return
    explicit execution receipts.
    """
    bundle = payload.get("action_bundle") or payload.get("actionBundle") or {}
    items = bundle.get("items") if isinstance(bundle, dict) else payload.get("items")
    if not isinstance(items, list) or not items:
        raise HTTPException(status_code=400, detail="action_bundle.items 不能为空")

    plan_summary = (payload.get("plan_summary") or payload.get("planSummary") or "AI规划行程").strip()
    city = (payload.get("city") or current_user.get("city") or "北京").strip() or "北京"
    today_date = datetime.now().strftime("%Y-%m-%d")
    executed = []
    reservations_to_create = []
    gift_orders_to_create = []

    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        action_type = str(raw_item.get("type") or "").strip()
        target = str(raw_item.get("target") or raw_item.get("title") or "").strip()
        payload_data = raw_item.get("payload") if isinstance(raw_item.get("payload"), dict) else {}

        if action_type == "reserve":
            place_name = str(payload_data.get("place_name") or target).strip()
            if not place_name:
                continue
            reservations_to_create.append({
                "place_name": place_name,
                "time": str(payload_data.get("time") or raw_item.get("time") or "").strip(),
                "date": str(payload_data.get("date") or raw_item.get("date") or today_date).strip(),
                "people_count": payload_data.get("people_count") or raw_item.get("people_count") or 1,
                "price": str(payload_data.get("price") or raw_item.get("price") or "").strip(),
                "city": city,
            })
        elif action_type == "order_gift":
            gift_name = target or "鲜花/蛋糕"
            deliver_to = str(payload_data.get("deliver_to") or "待确认地点").strip()
            reason = str(payload_data.get("reason") or raw_item.get("message") or "用于本次 AI 规划路线的惊喜安排").strip()
            gift_orders_to_create.append({
                "place_name": deliver_to,
                "gift_name": gift_name,
                "desc": f"附加下单：{gift_name} · 购买方式：到店/配送到「{deliver_to}」 · 安排原因：{reason}",
                "date": today_date,
                "price": str(payload_data.get("price") or raw_item.get("price") or "待结算").strip()[:120],
                "city": city,
                "target": gift_name,
            })
        elif action_type == "notify":
            executed.append({
                "type": "notify",
                "target": target or "朋友",
                "status": "mock_success",
                "message": raw_item.get("message") or f"已模拟通知{target or '朋友'}。",
            })
        elif action_type == "share":
            executed.append({
                "type": "share",
                "target": target or "联系人",
                "status": "mock_success",
                "message": raw_item.get("message") or f"已生成发给{target or '联系人'}的确认文案。",
            })
        elif action_type == "sms":
            phone = str(payload_data.get("phone") or raw_item.get("phone") or "").strip()
            message = str(payload_data.get("message") or raw_item.get("message") or "").strip()
            if not phone or not message:
                executed.append({
                    "type": "sms",
                    "target": target or "手机号",
                    "status": "mock_skipped",
                    "message": "短信动作缺少手机号或内容，已跳过。",
                })
            else:
                try:
                    sms_result = await send_sms(phone=phone, message=message, scene="plan_notify")
                except ValueError as exc:
                    sms_result = {
                        "type": "sms",
                        "status": "failed",
                        "target": target or "手机号",
                        "message": str(exc),
                    }
                executed.append({
                    "type": "sms",
                    "target": target or sms_result.get("phone") or "手机号",
                    "status": sms_result.get("status"),
                    "message": sms_result.get("message") or "短信动作已处理。",
                    "receipt": sms_result,
                })
        elif action_type in ("share_route", "save_calendar"):
            # 客户端动作（生成二维码、写入日历），不应到达服务端，在此静默成功
            executed.append({
                "type": action_type,
                "target": target,
                "status": "client_side",
                "message": "分享/日历动作已在客户端处理。",
            })
        else:
            executed.append({
                "type": action_type or "unknown",
                "target": target,
                "status": "mock_skipped",
                "message": "未知动作类型，已跳过。",
            })

    created_orders = []
    if reservations_to_create or gift_orders_to_create:
        conn = get_db()
        try:
            with conn.cursor() as cursor:
                for item in reservations_to_create:
                    place_name = item["place_name"][:128]
                    reserve_time = item["time"][:20]
                    reserve_date = item["date"] or today_date
                    people_count = item["people_count"] or 1
                    price = item["price"][:120]
                    existing_id = _find_existing_order(cursor, current_user["id"], place_name, reserve_date)
                    if existing_id:
                        executed.append({
                            "type": "reserve",
                            "target": place_name,
                            "status": "duplicate_skipped",
                            "message": f"已存在行程：{place_name}，本次避免重复预约。",
                            "order": {"id": existing_id, "place_name": place_name, "date": reserve_date},
                        })
                        continue
                    order_id = str(int(time.time() * 1000)) + str(len(created_orders))
                    date_prefix = f"{reserve_date} " if reserve_date != today_date else ""
                    desc = f"{date_prefix}{reserve_time} · {people_count}人" if reserve_time else f"{people_count}人"
                    cursor.execute(
                        "INSERT INTO orders (id, ico, title, desc_text, date_str, status, statusText, price, tags, user_id, plan_summary, city, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                        (
                            order_id, "📍", place_name, desc,
                            reserve_date, "ongoing", "进行中",
                            price or "待结算",
                            json.dumps(["AI规划", "预约"]),
                            current_user["id"],
                            plan_summary,
                            city,
                        ),
                    )
                    created = {
                        "id": order_id,
                        "place_name": place_name,
                        "time": reserve_time,
                        "date": reserve_date,
                        "people_count": people_count,
                        "price": price or "待结算",
                        "city": city,
                        "status": "ongoing",
                    }
                    created_orders.append(created)
                    executed.append({
                        "type": "reserve",
                        "target": place_name,
                        "status": "mock_success",
                        "message": f"已加入行程：{place_name} {reserve_time or ''}。",
                        "order": created,
                    })
                for item in gift_orders_to_create:
                    title = (item["place_name"] or item["gift_name"] or "待确认地点")[:128]
                    date_str = item["date"] or today_date
                    existing_id = _find_existing_order(cursor, current_user["id"], title, date_str)
                    if existing_id:
                        cursor.execute(
                            "SELECT desc_text FROM orders WHERE id = %s AND user_id = %s LIMIT 1",
                            (existing_id, current_user["id"]),
                        )
                        existing_row = cursor.fetchone() or {}
                        existing_desc = str(existing_row.get("desc_text") or "")
                        gift_name = item["gift_name"][:128]
                        if gift_name and gift_name in existing_desc:
                            executed.append({
                                "type": "order_gift",
                                "target": title,
                                "status": "duplicate_skipped",
                                "message": f"「{title}」卡片里已包含{gift_name}，本次避免重复下单。",
                                "order": {"id": existing_id, "title": title, "date": date_str},
                            })
                            continue
                        next_desc = f"{existing_desc} · {item['desc']}" if existing_desc else item["desc"]
                        cursor.execute(
                            "UPDATE orders SET desc_text = %s, price = %s, tags = %s WHERE id = %s AND user_id = %s",
                            (
                                next_desc,
                                item["price"] or "待结算",
                                json.dumps(["AI规划", "预约", "下单"]),
                                existing_id,
                                current_user["id"],
                            ),
                        )
                        executed.append({
                            "type": "order_gift",
                            "target": title,
                            "status": "mock_success",
                            "message": f"已更新「{title}」行程卡片：{item['desc']}",
                            "order": {"id": existing_id, "title": title, "date": date_str},
                        })
                        continue
                    order_id = str(int(time.time() * 1000)) + str(len(created_orders))
                    cursor.execute(
                        "INSERT INTO orders (id, ico, title, desc_text, date_str, status, statusText, price, tags, user_id, plan_summary, city, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())",
                        (
                            order_id, "🎁", title, item["desc"],
                            date_str, "ongoing", "进行中",
                            item["price"] or "待结算",
                            json.dumps(["AI规划", "下单"]),
                            current_user["id"],
                            plan_summary,
                            item["city"] or city,
                        ),
                    )
                    created = {
                        "id": order_id,
                        "title": title,
                        "desc": item["desc"],
                        "date": date_str,
                        "price": item["price"] or "待结算",
                        "city": item["city"] or city,
                        "status": "ongoing",
                    }
                    created_orders.append(created)
                    executed.append({
                        "type": "order_gift",
                        "target": title,
                        "status": "mock_success",
                        "message": f"已创建「{title}」行程卡片并记录：{item['desc']}",
                        "order": created,
                    })
            conn.commit()
        except pymysql.Error as e:
            logger.error(f"执行动作清单时数据库错误: {e}")
            raise HTTPException(status_code=500, detail="动作执行失败")
        finally:
            conn.close()

    logger.info(f"⚡ 用户 {current_user['name']} 一键执行 AI 动作 {len(executed)} 个（{plan_summary}）")
    return {
        "code": 200,
        "message": "动作已执行",
        "data": {
            "executed": executed,
            "orders": created_orders,
        },
    }


@router.delete("/{orderId}")
async def delete_order(orderId: str, current_user: dict = Depends(get_current_user)):
    """Delete an order."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM orders WHERE id = %s AND user_id = %s", (orderId, current_user["id"]))
            affected = cursor.rowcount
        conn.commit()
        if affected == 0:
            raise HTTPException(status_code=404, detail="订单不存在或无权删除")
        logger.info(f"🗑️ 删除订单: 订单号 {orderId}, 用户 {current_user['name']}")
        return {"code": 200, "message": "success", "data": {"deletedId": orderId}}
    except HTTPException:
        raise
    except pymysql.Error as e:
        logger.error(f"删除订单时数据库错误: {e}")
        raise HTTPException(status_code=500, detail="删除失败")
    finally:
        conn.close()

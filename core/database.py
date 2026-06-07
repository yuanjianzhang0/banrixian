# -*- coding: utf-8 -*-
"""Database connection pool and bootstrap logic."""

from __future__ import annotations

import json
import logging
import random
import re
import time
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

from core.config import (
    DATABASE_CHARSET,
    DATABASE_HOST,
    DATABASE_MAX_CACHED,
    DATABASE_MAX_CONNECTIONS,
    DATABASE_MIN_CACHED,
    DATABASE_NAME,
    DATABASE_PASSWORD,
    DATABASE_PORT,
    DATABASE_USER,
)


logger = logging.getLogger("BanrixianAPI")

MYSQL_POOL: Any | None = None
_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def get_pool():
    """Create the connection pool lazily so importing the app stays lightweight."""
    global MYSQL_POOL
    if MYSQL_POOL is None:
        from dbutils.pooled_db import PooledDB

        MYSQL_POOL = PooledDB(
            creator=pymysql,
            maxconnections=DATABASE_MAX_CONNECTIONS,
            mincached=DATABASE_MIN_CACHED,
            maxcached=DATABASE_MAX_CACHED,
            blocking=True,
            host=DATABASE_HOST,
            port=DATABASE_PORT,
            user=DATABASE_USER,
            password=DATABASE_PASSWORD,
            database=DATABASE_NAME,
            charset=DATABASE_CHARSET,
            cursorclass=DictCursor,
        )
    return MYSQL_POOL


def get_db():
    """Return a pooled raw PyMySQL connection."""
    return get_pool().connection()


def generate_numeric_id():
    return int(f"{int(time.time() * 10) % 100000000:08d}{random.randint(1000, 9999)}")


def _quote_identifier(identifier: str) -> str:
    if not _SQL_IDENTIFIER_RE.fullmatch(identifier):
        raise ValueError(f"Unsafe SQL identifier: {identifier}")
    return f"`{identifier}`"


def _ensure_column(cursor, table_name: str, column_name: str, definition: str) -> None:
    table_sql = _quote_identifier(table_name)
    column_sql = _quote_identifier(column_name)
    cursor.execute(f"SHOW COLUMNS FROM {table_sql} LIKE %s", (column_name,))
    if cursor.fetchone():
        return
    cursor.execute(f"ALTER TABLE {table_sql} ADD COLUMN {column_sql} {definition}")
    logger.info("已为表 %s 补齐缺失字段 %s", table_name, column_name)


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def init_db():
    logger.info("🚀 正在检查并初始化 MySQL 生产环境数据库结构...")
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            # 用户表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id VARCHAR(64) PRIMARY KEY, 
                    openid VARCHAR(128) UNIQUE,
                    username VARCHAR(64) UNIQUE,
                    password VARCHAR(255), 
                    name VARCHAR(64), 
                    avatar TEXT,
                    city VARCHAR(64),
                    phone VARCHAR(32),  
                    pts INT DEFAULT 0, 
                    tags TEXT
                )
            """)
            try:
                cursor.execute("ALTER TABLE users MODIFY password VARCHAR(255)")
            except pymysql.Error as e:
                logger.warning("调整 users.password 字段长度失败或无需调整: %s", e)
            
            # Token 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_tokens (
                    token VARCHAR(128) PRIMARY KEY,
                    user_id VARCHAR(64),
                    expire_time DOUBLE
                )
            """)
            
            # 服务表 (注意：desc 改为了 desc_text 规避 MySQL 关键字)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS services (
                    id INT PRIMARY KEY AUTO_INCREMENT, 
                    ico VARCHAR(16), 
                    name VARCHAR(64), 
                    bg VARCHAR(64), 
                    desc_text VARCHAR(255), 
                    opts TEXT
                )
            """)
            
            # 附近 POI 表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS nearby_pois (
                    id INT PRIMARY KEY AUTO_INCREMENT, 
                    ico VARCHAR(16), 
                    name VARCHAR(128), 
                    score VARCHAR(16), 
                    tags VARCHAR(64),
                    total_seats INT DEFAULT 0
                )
            """)
            _ensure_column(cursor, "nearby_pois", "total_seats", "INT DEFAULT 0")
            if _table_exists(cursor, "places"):
                _ensure_column(cursor, "places", "city", "VARCHAR(64) DEFAULT '北京'")
                _ensure_column(cursor, "places", "source", "VARCHAR(32) DEFAULT 'seed'")
                _ensure_column(cursor, "places", "source_id", "VARCHAR(128)")
                _ensure_column(cursor, "places", "district", "VARCHAR(64)")
                _ensure_column(cursor, "places", "business_area", "VARCHAR(128)")
                _ensure_column(cursor, "places", "tags", "TEXT")
                _ensure_column(cursor, "places", "phone", "VARCHAR(128)")
                _ensure_column(cursor, "places", "updated_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
                cursor.execute("UPDATE places SET city = '北京' WHERE city IS NULL OR city = ''")
                cursor.execute("UPDATE places SET source = 'seed' WHERE source IS NULL OR source = ''")
            _ensure_column(cursor, "orders", "plan_summary", "TEXT")
            _ensure_column(cursor, "orders", "created_at", "DATETIME DEFAULT CURRENT_TIMESTAMP")
            _ensure_column(cursor, "orders", "city", "VARCHAR(64) DEFAULT '北京'")
            # 扩展 price 列，避免长价格字符串写入失败
            cursor.execute("ALTER TABLE orders MODIFY COLUMN price VARCHAR(128)")
            cursor.execute("UPDATE orders SET city = '北京' WHERE city IS NULL OR city = ''")

            # POI 容量状态表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS place_capacity_status (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    place_id INT,
                    available INT DEFAULT NULL,
                    queue_count INT DEFAULT 0,
                    status VARCHAR(32) DEFAULT 'available',
                    updated_at DATETIME,
                    INDEX idx_place_id (place_id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS poi_import_runs (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    source VARCHAR(32),
                    city VARCHAR(64),
                    keyword VARCHAR(128),
                    pages INT DEFAULT 0,
                    offset_count INT DEFAULT 0,
                    imported_count INT DEFAULT 0,
                    status VARCHAR(32),
                    error_text TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY uniq_poi_import (source, city, keyword)
                )
            """)
            
            # 订单表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id VARCHAR(64) PRIMARY KEY, 
                    ico VARCHAR(16), 
                    title VARCHAR(128), 
                    desc_text TEXT, 
                    date_str VARCHAR(32), 
                    status VARCHAR(32), 
                    statusText VARCHAR(32), 
                    price VARCHAR(32), 
                    tags TEXT, 
                    user_id VARCHAR(64)
                )
            """)
            
            # 热搜表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS hot_list (
                    id INT PRIMARY KEY AUTO_INCREMENT, 
                    title VARCHAR(128), 
                    desc_text VARCHAR(255)
                )
            """)
            
            # 亲友库表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS family_members (
                    id INT PRIMARY KEY AUTO_INCREMENT, 
                    user_id VARCHAR(64), 
                    avatar VARCHAR(16), 
                    relation VARCHAR(64), 
                    tags TEXT
                )
            """)

            # AI 长期偏好记忆表：只记录较稳定的用户/亲友偏好，不保存整段对话。
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS agent_memories (
                    id INT PRIMARY KEY AUTO_INCREMENT,
                    user_id VARCHAR(64),
                    memory_type VARCHAR(32),
                    title VARCHAR(128),
                    content TEXT,
                    relation VARCHAR(64),
                    source VARCHAR(64),
                    weight INT DEFAULT 1,
                    created_at DATETIME,
                    updated_at DATETIME,
                    UNIQUE KEY uniq_user_memory (user_id, memory_type, title, relation)
                )
            """)
            
            # 聊天记录云同步表（每用户一行，存最近 60 条消息 + 最后路线）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_chat_sync (
                    user_id VARCHAR(64) PRIMARY KEY,
                    messages TEXT,
                    last_plan TEXT,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                )
            """)

            # 行程分享表（短 ID 对应完整路线，无需登录可查看）
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS shared_routes (
                    share_id VARCHAR(16) PRIMARY KEY,
                    user_id VARCHAR(64),
                    title VARCHAR(255),
                    plan_json MEDIUMTEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    expires_at DATETIME,
                    view_count INT DEFAULT 0
                )
            """)

            # 预注入个性化热搜数据
            cursor.execute("SELECT id FROM hot_list LIMIT 1")
            if not cursor.fetchone():
                cursor.execute("INSERT INTO hot_list (title, desc_text) VALUES (%s, %s)", ('极光亲子乐园限时5折', '仅剩 23 张 · 今日截止'))
                cursor.execute("INSERT INTO hot_list (title, desc_text) VALUES (%s, %s)", ('拾味海鲜蒸汽锅预约攻略', '周末排队 2h · 提前预约省时'))
                cursor.execute("INSERT INTO hot_list (title, desc_text) VALUES (%s, %s)", ('太阳宫新晋网红咖啡馆', '多巴胺穿搭打卡圣地'))

            # 预注入标准业务数据
            cursor.execute("SELECT id FROM services LIMIT 1")
            if not cursor.fetchone():
                default_services = [
                    { "ico": "🏨", "name": "酒店预订", "bg": "rgba(255,149,0,0.1)", "desc": "AI智能匹配最优房型", "opts": [{ "name": "豪华大床房", "price": "688" }, { "name": "行政套房", "price": "1288" }] },
                    { "ico": "🎟️", "name": "景点门票", "bg": "rgba(0,122,255,0.1)", "desc": "实时查询余票，秒速出票", "opts": [{ "name": "成人票", "price": "118" }, { "name": "儿童票", "price": "59" }] },
                    { "ico": "🥘", "name": "美食团购", "bg": "rgba(255,45,85,0.1)", "desc": "基于您的口味偏好精选", "opts": [{ "name": "双人套餐", "price": "198" }, { "name": "工作餐", "price": "68" }] }
                ]
                for s in default_services:
                    cursor.execute("INSERT INTO services (ico, name, bg, desc_text, opts) VALUES (%s, %s, %s, %s, %s)", 
                                   (s["ico"], s["name"], s["bg"], s["desc"], json.dumps(s["opts"])))
                    
            cursor.execute("SELECT id FROM nearby_pois LIMIT 1")
            if not cursor.fetchone():
                cursor.execute("INSERT INTO nearby_pois (ico, name, score, tags) VALUES (%s, %s, %s, %s)", ('🏯', '极光亲子探险乐园', '4.9', '亲子游乐'))
                cursor.execute("INSERT INTO nearby_pois (ico, name, score, tags) VALUES (%s, %s, %s, %s)", ('🍱', '拾味海鲜蒸汽锅', '4.8', '海鲜排挡'))
            
        conn.commit()
    finally:
        conn.close()
    logger.info("📂 MySQL 数据库初始化完毕！")

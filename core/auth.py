# -*- coding: utf-8 -*-
"""Authentication dependencies shared by routers."""

from __future__ import annotations

import logging

import pymysql
from fastapi import Header, HTTPException

from core.database import get_db


logger = logging.getLogger("BanrixianAPI")


async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="未提供身份认证凭证")
    
    token = authorization.split(" ")[1]
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            try:
                cursor.execute("SELECT user_id FROM user_tokens WHERE token = %s", (token,))
                token_row = cursor.fetchone()
                if not token_row:
                    raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
                
                user_id = token_row["user_id"]
                cursor.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                user_row = cursor.fetchone()
                if not user_row:
                    raise HTTPException(status_code=401, detail="用户不存在")
                    
                return user_row
            except pymysql.Error as e:
                logger.error(f"数据库查询异常: {e}")
                raise HTTPException(status_code=500, detail="数据库访问失败")
    finally:
        conn.close()


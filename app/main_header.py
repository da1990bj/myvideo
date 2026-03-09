from typing import Annotated, List, Optional
from jose import JWTError, jwt
from fastapi import FastAPI, Depends, HTTPException, status, UploadFile, File, Form, Query
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlmodel import Session, select, desc
import shutil
import os
from uuid import uuid4

from database import engine, get_session, init_db
# 这里的导入列表必须与 models.py 严格一致
from models import User, UserCreate, UserRead, UserLogin, Token, Video, VideoStatus, VideoRead, Category, VideoUpdate, VideoVisibility
from security import get_password_hash, verify_password, create_access_token, ACCESS_TOKEN_EXPIRE_MINUTES, SECRET_KEY, ALGORITHM
from tasks import transcode_video_task
from init_data import init_categories

# ... (其余 200 行代码保持不变，但我用 edit 替换导入部分)

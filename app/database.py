from sqlmodel import SQLModel, create_engine, Session
from config import settings

# Database URL from settings (supports env var override)
# 使用 connect_args 设置时区为 Asia/Shanghai
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DATABASE_ECHO,
    connect_args={"options": "-c timezone=Asia/Shanghai"}
)

# Backward compatibility alias
DATABASE_URL = settings.DATABASE_URL

def get_session():
    """依赖项: 获取数据库会话"""
    with Session(engine) as session:
        yield session

def init_db():
    """初始化数据库: 创建所有表"""
    SQLModel.metadata.create_all(engine)

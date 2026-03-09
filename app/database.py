from sqlmodel import SQLModel, create_engine, Session

# 连接到本地 Docker 映射的 Postgres 端口
# 格式: postgresql://user:password@host:port/dbname
DATABASE_URL = "postgresql://myvideo:myvideo_password@localhost:5432/myvideo_db"

# echo=True 表示在控制台打印 SQL 语句，方便调试
engine = create_engine(DATABASE_URL, echo=True)

def get_session():
    """依赖项: 获取数据库会话"""
    with Session(engine) as session:
        yield session

def init_db():
    """初始化数据库: 创建所有表"""
    SQLModel.metadata.create_all(engine)

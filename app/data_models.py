from typing import Optional, List
from datetime import datetime
from uuid import UUID, uuid4
from sqlmodel import Field, SQLModel, Relationship, JSON, Column

# User
class User(SQLModel, table=True):
    __tablename__ = "users"
    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: str = Field(unique=True)
    hashed_password: str
    avatar_path: Optional[str] = None
    bio: Optional[str] = None
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False) # 新增管理员字段
    created_at: datetime = Field(default_factory=datetime.utcnow)
    videos: List["Video"] = Relationship(back_populates="owner")

# Category
class Category(SQLModel, table=True):
    __tablename__ = "categories"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    slug: str = Field(unique=True)
    videos: List["Video"] = Relationship(back_populates="category")

# Video
class Video(SQLModel, table=True):
    __tablename__ = "videos"

    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    title: str = Field(index=True)
    description: Optional[str] = None
    
    original_file_path: str 
    processed_file_path: Optional[str] = None
    thumbnail_path: Optional[str] = None
    
    status: str = Field(default="pending")
    visibility: str = Field(default="public")
    
    task_id: Optional[str] = None
    views: int = Field(default=0)
    complete_views: int = Field(default=0)
    progress: int = Field(default=0)
    duration: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    tags: List[str] = Field(default=[], sa_column=Column(JSON))

    user_id: UUID = Field(foreign_key="users.id")
    owner: User = Relationship(back_populates="videos")
    
    category_id: Optional[int] = Field(default=None, foreign_key="categories.id")
    category: Optional[Category] = Relationship(back_populates="videos")

# 社交关系表
class UserFollow(SQLModel, table=True):
    __tablename__ = "user_follows"
    follower_id: UUID = Field(foreign_key="users.id", primary_key=True)
    followed_id: UUID = Field(foreign_key="users.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)



class UserBlock(SQLModel, table=True):
    __tablename__ = "user_blocks"
    blocker_id: UUID = Field(foreign_key="users.id", primary_key=True)
    blocked_id: UUID = Field(foreign_key="users.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

# 观看历史
class UserVideoHistory(SQLModel, table=True):
    __tablename__ = "user_video_history"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", primary_key=True)
    progress: float = Field(default=0.0) # 观看进度(秒)
    last_watched: datetime = Field(default_factory=datetime.utcnow)
    is_finished: bool = Field(default=False)

# 评论系统
class Comment(SQLModel, table=True):
    __tablename__ = "comments"
    id: Optional[int] = Field(default=None, primary_key=True)
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    user_id: UUID = Field(foreign_key="users.id")
    owner: User = Relationship()

    video_id: UUID = Field(foreign_key="videos.id")
    video: Video = Relationship()

# 点赞系统
class VideoLike(SQLModel, table=True):
    __tablename__ = "video_likes"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", primary_key=True)
    like_type: str = Field(default="like") # "like" or "dislike"
    created_at: datetime = Field(default_factory=datetime.utcnow)

# 通知系统
class Notification(SQLModel, table=True):
    __tablename__ = "notifications"
    id: Optional[int] = Field(default=None, primary_key=True)
    recipient_id: UUID = Field(foreign_key="users.id", index=True) # 接收者
    sender_id: UUID = Field(foreign_key="users.id") # 触发者
    type: str = Field(index=True) # "follow", "comment", "like_video"
    entity_id: Optional[str] = None # 关联对象ID (video_id, etc)
    is_read: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # 关系 (可选，为了方便查询发送者信息)
    sender: User = Relationship(sa_relationship_kwargs={"foreign_keys": "Notification.sender_id"})

# Schemas
class UserCreate(SQLModel):
    username: str
    email: str
    password: str

class UserLogin(SQLModel):
    username: str
    password: str

class UserRead(SQLModel):
    id: UUID
    username: str
    email: str
    is_active: bool
    is_admin: bool = False
    created_at: datetime
    avatar_path: Optional[str]
    bio: Optional[str] = None



class UserUpdate(SQLModel):
    bio: Optional[str] = None

class UserPasswordUpdate(SQLModel):
    old_password: str
    new_password: str

class EmailUpdate(SQLModel):
    new_email: str

class Token(SQLModel):
    access_token: str
    token_type: str

class VideoUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    visibility: Optional[str] = None
    tags: Optional[List[str]] = None

class VideoRead(SQLModel):
    id: UUID
    title: str
    description: Optional[str]
    status: str
    visibility: str
    processed_file_path: Optional[str]
    thumbnail_path: Optional[str]
    duration: Optional[int]
    views: int
    complete_views: int
    progress: int = 0
    created_at: datetime
    tags: List[str] = []
    owner: Optional[UserRead] = None
    category: Optional[Category] = None

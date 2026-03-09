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

# 视频点赞/踩
class VideoLike(SQLModel, table=True):
    __tablename__ = "video_likes"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", primary_key=True)
    like_type: str # "like" or "dislike"
    created_at: datetime = Field(default_factory=datetime.utcnow)

class UserBlock(SQLModel, table=True):
    __tablename__ = "user_blocks"
    blocker_id: UUID = Field(foreign_key="users.id", primary_key=True)
    blocked_id: UUID = Field(foreign_key="users.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

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
    created_at: datetime
    avatar_path: Optional[str]
    bio: Optional[str] = None

class UserReadProfile(UserRead):
    is_following: bool = False
    followers_count: int = 0
    following_count: int = 0
    video_count: int = 0

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
    likes_count: Optional[int] = None
    dislikes_count: Optional[int] = None

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
    likes_count: int = 0
    dislikes_count: int = 0
    is_liked_by_current_user: bool = False
    is_disliked_by_current_user: bool = False

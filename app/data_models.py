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
    role_id: Optional[int] = Field(default=None, foreign_key="roles.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    videos: List["Video"] = Relationship(back_populates="owner")
    collections: List["Collection"] = Relationship(back_populates="owner")
    role: Optional["Role"] = Relationship(back_populates="users")

# Role
class Role(SQLModel, table=True):
    __tablename__ = "roles"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    description: Optional[str] = None
    permissions: str = Field(default="") # Comma separated list of permissions
    created_at: datetime = Field(default_factory=datetime.utcnow)
    users: List["User"] = Relationship(back_populates="role")

# System Configuration
class SystemConfig(SQLModel, table=True):
    __tablename__ = "system_config"
    key: str = Field(primary_key=True)
    value: str
    description: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# Admin Audit Log
class AdminLog(SQLModel, table=True):
    __tablename__ = "admin_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    admin_id: UUID = Field(foreign_key="users.id")
    action: str
    target_id: Optional[str] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    admin: User = Relationship()

# Category
class Category(SQLModel, table=True):
    __tablename__ = "categories"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    slug: str = Field(unique=True)
    videos: List["Video"] = Relationship(back_populates="category")

# --- New Tag Architecture ---
class VideoTag(SQLModel, table=True):
    __tablename__ = "video_tags"
    video_id: UUID = Field(foreign_key="videos.id", primary_key=True)
    tag_id: int = Field(foreign_key="tags.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class Tag(SQLModel, table=True):
    __tablename__ = "tags"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    usage_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    videos: List["Video"] = Relationship(back_populates="tags_rel", link_model=VideoTag)

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
    ban_reason: Optional[str] = None # 下架原因

    task_id: Optional[str] = None
    views: int = Field(default=0)
    complete_views: int = Field(default=0)
    like_count: int = Field(default=0)
    favorite_count: int = Field(default=0)
    progress: int = Field(default=0)
    duration: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # tags: List[str] = Field(default=[], sa_column=Column(JSON)) # Removed
    tags_rel: List[Tag] = Relationship(back_populates="videos", link_model=VideoTag)

    user_id: UUID = Field(foreign_key="users.id")
    owner: User = Relationship(back_populates="videos")

    category_id: Optional[int] = Field(default=None, foreign_key="categories.id")
    category: Optional[Category] = Relationship(back_populates="videos")

    # 兼容性属性：VideoRead 期望 tags 是 List[str]
    @property
    def tags(self) -> List[str]:
        return [t.name for t in self.tags_rel]

# 审核记录
class VideoAuditLog(SQLModel, table=True):
    __tablename__ = "video_audit_logs"
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id")
    operator_id: UUID = Field(foreign_key="users.id") # 操作者(管理员或作者)
    action: str # "ban", "approve", "appeal"
    reason: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    operator: User = Relationship()

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

    parent_id: Optional[int] = Field(default=None, foreign_key="comments.id")
    like_count: int = Field(default=0)

    is_deleted: bool = Field(default=False)
    deleted_by: Optional[str] = Field(default=None) # "user" or "admin"

    user_id: UUID = Field(foreign_key="users.id")
    owner: User = Relationship()

    video_id: UUID = Field(foreign_key="videos.id")
    video: Video = Relationship()

    # Self-referential relationship for nested comments
    # parent: Optional["Comment"] = Relationship(back_populates="replies", sa_relationship_kwargs={"remote_side": "Comment.id"})
    # replies: List["Comment"] = Relationship(back_populates="parent")

# 评论点赞
class CommentLike(SQLModel, table=True):
    __tablename__ = "comment_likes"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    comment_id: int = Field(foreign_key="comments.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

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
    content: Optional[str] = None # Stores comment/reply content
    is_read: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # 关系 (可选，为了方便查询发送者信息)
    sender: User = Relationship(sa_relationship_kwargs={"foreign_keys": "Notification.sender_id"})

# Collection
class Collection(SQLModel, table=True):
    __tablename__ = "collections"
    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    title: str = Field(index=True)
    description: Optional[str] = None
    cover_image: Optional[str] = None
    favorite_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    user_id: UUID = Field(foreign_key="users.id")
    owner: User = Relationship(back_populates="collections")

    items: List["CollectionItem"] = Relationship(back_populates="collection")

# Collection Item
class CollectionItem(SQLModel, table=True):
    __tablename__ = "collection_items"
    collection_id: UUID = Field(foreign_key="collections.id", primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", primary_key=True)
    order: int = Field(default=0)
    added_at: datetime = Field(default_factory=datetime.utcnow)

    collection: Collection = Relationship(back_populates="items")
    video: Video = Relationship()

# 视频收藏
class VideoFavorite(SQLModel, table=True):
    __tablename__ = "video_favorites"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

# 合集收藏
class CollectionFavorite(SQLModel, table=True):
    __tablename__ = "collection_favorites"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    collection_id: UUID = Field(foreign_key="collections.id", primary_key=True)
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
    like_count: int = 0
    favorite_count: int = 0
    progress: int = 0
    is_liked: bool = False
    is_favorited: bool = False
    created_at: datetime
    tags: List[str] = []
    owner: Optional[UserRead] = None
    category: Optional[Category] = None
    collection_id: Optional[UUID] = None # Return collection ID if video belongs to one

class CollectionCreate(SQLModel):
    title: str
    description: Optional[str] = None

class CollectionRead(SQLModel):
    id: UUID
    title: str
    description: Optional[str]
    cover_image: Optional[str]
    created_at: datetime
    video_count: int = 0
    favorite_count: int = 0
    is_favorited: bool = False
    owner: Optional[UserRead] = None
    first_video_id: Optional[UUID] = None # Added for frontend navigation

# RBAC Schemas
class RoleBase(SQLModel):
    name: str
    description: Optional[str] = None
    permissions: str = ""

class RoleCreate(RoleBase):
    pass

class RoleRead(RoleBase):
    id: int
    created_at: datetime
    user_count: int = 0 # Computed field

class RoleUpdate(SQLModel):
    name: Optional[str] = None
    description: Optional[str] = None
    permissions: Optional[str] = None

# System Config Schemas
class SystemConfigRead(SQLModel):
    key: str
    value: str
    description: Optional[str] = None
    updated_at: datetime

class SystemConfigUpdate(SQLModel):
    value: str
    description: Optional[str] = None

# Admin Log Schemas
class AdminLogRead(SQLModel):
    id: int
    admin_id: UUID
    admin_username: Optional[str] = None # Computed
    action: str
    target_id: Optional[str] = None
    details: Optional[str] = None
    ip_address: Optional[str] = None
    created_at: datetime



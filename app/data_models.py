from typing import Optional, List
from datetime import datetime, date
from uuid import UUID, uuid4
from sqlmodel import Field, SQLModel, Relationship, JSON, Column
from sqlalchemy import BigInteger

# User-Role Association Table (many-to-many)
class UserRole(SQLModel, table=True):
    __tablename__ = "user_roles"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True)
    role_id: int = Field(foreign_key="roles.id", primary_key=True)

    user: "User" = Relationship(back_populates="user_roles")
    role: "Role" = Relationship(back_populates="user_roles")

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
    is_vip: bool = Field(default=False)  # VIP用户标识
    credits: int = Field(default=0)  # 用户积分（用于插队等功能）
    private_videos_password_hash: Optional[str] = None  # 私密视频区域密码
    created_at: datetime = Field(default_factory=datetime.utcnow)
    videos: List["Video"] = Relationship(back_populates="owner")
    collections: List["Collection"] = Relationship(back_populates="owner")
    user_roles: List["UserRole"] = Relationship(back_populates="user")

# Role
class Role(SQLModel, table=True):
    __tablename__ = "roles"
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True)
    description: Optional[str] = None
    permissions: str = Field(default="") # Comma separated list of permissions
    created_at: datetime = Field(default_factory=datetime.utcnow)
    user_roles: List["UserRole"] = Relationship(back_populates="role")

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

    status: str = Field(default="pending")  # 转码状态: pending, processing, paused, completed, failed
    is_approved: str = Field(default="pending", index=True)  # 审核状态: pending, approved, banned, appealing
    is_deleted: bool = Field(default=False, index=True)  # 软删除
    visibility: str = Field(default="public")
    ban_reason: Optional[str] = None  # 下架原因

    task_id: Optional[str] = None
    views: int = Field(default=0)
    complete_views: int = Field(default=0)
    like_count: int = Field(default=0)
    favorite_count: int = Field(default=0)
    progress: int = Field(default=0)
    duration: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Cold storage fields
    is_cold: bool = Field(default=False, index=True)  # Video is in cold storage
    cold_stored_at: Optional[datetime] = None  # When video was moved to cold storage

    # Soft delete
    deleted_at: Optional[datetime] = None  # When video was soft deleted

    # Subtitle fields
    subtitle_languages: Optional[List[str]] = Field(default=None, sa_column=Column(JSON))  # e.g. ["en", "zh-Hans", "zh-Hant"]
    auto_subtitle: bool = Field(default=False)  # Whether auto-subtitle was generated
    auto_subtitle_language: Optional[str] = None  # Language used for auto-subtitle
    subtitle_task_id: Optional[str] = None  # Celery task ID for subtitle generation

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

# 匿名用户播放记录（防刷）
class AnonymousViewHistory(SQLModel, table=True):
    __tablename__ = "anonymous_view_history"
    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    anonymous_id: str = Field(index=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)
    view_count: int = Field(default=1)
    first_viewed_at: datetime = Field(default_factory=datetime.utcnow)
    last_viewed_at: datetime = Field(default_factory=datetime.utcnow)

# 匿名播放统计Token（一次性）
class ViewToken(SQLModel, table=True):
    __tablename__ = "view_tokens"
    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    token: str = Field(unique=True, index=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)
    anonymous_id: str = Field(index=True)
    used: bool = Field(default=False)
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)

# 评论系统
class Comment(SQLModel, table=True):
    __tablename__ = "comments"
    id: Optional[int] = Field(default=None, primary_key=True)
    content: str
    created_at: datetime = Field(default_factory=datetime.utcnow)

    parent_id: Optional[int] = Field(default=None, foreign_key="comments.id")
    like_count: int = Field(default=0)
    dislike_count: int = Field(default=0)

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
    like_type: str = Field(default="like") # "like" or "dislike"
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
    is_public: bool = Field(default=True)
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

# ==================== 推荐系统表 ====================

# 视频推荐配置表（手动推荐）
class VideoRecommendation(SQLModel, table=True):
    __tablename__ = "video_recommendations"
    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)

    # 推荐位置和优先级
    recommendation_type: str  # "featured_carousel", "category_featured", "trending", "sidebar"
    slot_position: int = Field(default=0)  # 排序位置，越小越靠前
    priority: int = Field(default=5)  # 1-10，优先级

    # 推荐配置
    reason: str = Field(default="")  # 推荐理由
    enabled: bool = Field(default=True)
    expires_at: Optional[datetime] = None

    # 审计信息
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: UUID = Field(foreign_key="users.id")

# 推荐位配置表
class RecommendationSlot(SQLModel, table=True):
    __tablename__ = "recommendation_slots"
    id: Optional[int] = Field(default=None, primary_key=True)
    slot_name: str = Field(unique=True, index=True)  # "home_carousel", "sidebar_related"
    display_title: str = Field(default="")
    description: Optional[str] = None

    # 推荐位配置
    max_items: int = Field(default=10)
    recommendation_strategy: str = Field(default="manual_first")  # "manual_first", "algorithm_only", "mixed"

    # 登录状态相关配置
    show_authenticated: bool = Field(default=True)
    show_unauthenticated: bool = Field(default=True)
    unauthenticated_strategy: str = Field(default="trending_only")  # "trending_only", "popular_categories", "hidden"

    enabled: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

# 用户-视频推荐分数缓存表
class UserVideoScore(SQLModel, table=True):
    __tablename__ = "user_video_scores"
    user_id: UUID = Field(foreign_key="users.id", primary_key=True, index=True)
    video_id: UUID = Field(foreign_key="videos.id", primary_key=True, index=True)

    # 四个维度的推荐分数
    collaborative_score: float = Field(default=0.0)  # 协同过滤
    similarity_score: float = Field(default=0.0)     # 点赞/收藏相似
    category_score: float = Field(default=0.0)       # 分类偏好
    tag_score: float = Field(default=0.0)            # 标签偏好
    final_score: float = Field(default=0.0)          # 加权综合分

    # 控制信息
    last_updated: datetime = Field(default_factory=datetime.utcnow)

# 推荐日志表（用于分析）
class RecommendationLog(SQLModel, table=True):
    __tablename__ = "recommendation_logs"
    id: Optional[UUID] = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)

    # 推荐来源和位置
    recommendation_source: str  # "manual", "collaborative", "similarity", "category", "tag", "trending"
    slot_name: str = Field(default="")  # 推荐位置
    impression_rank: int = Field(default=0)  # 排名

    # 用户交互数据
    clicked: bool = Field(default=False)
    watched: bool = Field(default=False)
    clicked_at: Optional[datetime] = None
    watched_duration: Optional[float] = None

    # 时间戳
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

# 每日热门视频表
class DailyTrendingVideo(SQLModel, table=True):
    __tablename__ = "daily_trending_videos"
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)
    trending_date: date = Field(index=True)  # 日期
    score: float = Field(default=0.0)  # 热度分数
    views: int = Field(default=0)  # 当天观看数
    likes: int = Field(default=0)  # 当天点赞数
    favorites: int = Field(default=0)  # 当天收藏数
    created_at: datetime = Field(default_factory=datetime.utcnow)

# 分类热门视频表
class CategoryTrendingVideo(SQLModel, table=True):
    __tablename__ = "category_trending_videos"
    id: Optional[int] = Field(default=None, primary_key=True)
    category_id: int = Field(foreign_key="categories.id", index=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)
    trending_date: date = Field(index=True)
    score: float = Field(default=0.0)
    created_at: datetime = Field(default_factory=datetime.utcnow)

# 视频分享表
class VideoShare(SQLModel, table=True):
    __tablename__ = "video_shares"
    id: Optional[int] = Field(default=None, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)
    token: str = Field(unique=True, index=True)  # 分享token
    created_by: UUID = Field(foreign_key="users.id")  # 创建者
    expires_at: datetime = Field(nullable=True)  # 过期时间，null表示永不过期
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
    is_vip: bool = False
    role_ids: List[int] = []
    role_names: List[str] = []
    created_at: datetime
    avatar_path: Optional[str]
    bio: Optional[str] = None
    has_private_videos_password: bool = False  # 前端用此判断是否设置了私密视频密码



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

class TokenResponse(SQLModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"

class RefreshRequest(SQLModel):
    refresh_token: str

class RefreshToken(SQLModel, table=True):
    """Refresh Token 持久化表"""
    __tablename__ = "refresh_tokens"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    token_hash: str  # 存储哈希而非原始 token
    device_info: Optional[str] = None
    expires_at: datetime
    created_at: datetime = Field(default_factory=datetime.utcnow)
    revoked: bool = Field(default=False)

class VideoUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    visibility: Optional[str] = None
    tags: Optional[List[str]] = None
    temp_thumbnail_path: Optional[str] = None

class VideoRead(SQLModel):
    id: UUID
    title: str
    description: Optional[str]
    status: str
    is_approved: str = "pending"
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
    is_cold: bool = False
    cold_stored_at: Optional[datetime] = None
    tags: List[str] = []
    owner: Optional[UserRead] = None
    category: Optional[Category] = None
    collection_id: Optional[UUID] = None # Return collection ID if video belongs to one
    subtitle_languages: Optional[List[str]] = None
    auto_subtitle: bool = False
    auto_subtitle_language: Optional[str] = None


class SubtitleRead(SQLModel):
    """字幕文件信息"""
    language: str  # ISO language code (en, zh-Hans, zh-Hant, etc.)
    url: str  # URL path to the .vtt file
    is_auto_generated: bool = False


class SubtitleGenerateRequest(SQLModel):
    """自动生成字幕请求"""
    language: str = "en"  # Language code for Whisper

class CollectionCreate(SQLModel):
    title: str
    description: Optional[str] = None
    is_public: bool = True

class CollectionUpdate(SQLModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_public: Optional[bool] = None

class CollectionRead(SQLModel):
    id: UUID
    title: str
    description: Optional[str]
    cover_image: Optional[str]
    is_public: bool = True
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


# ==================== 推荐系统 Schema ====================

class VideoRecommendationRead(SQLModel):
    id: UUID
    video_id: UUID
    recommendation_type: str
    slot_position: int
    priority: int
    reason: str
    enabled: bool
    expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    created_by: UUID

class VideoRecommendationWithVideoRead(SQLModel):
    id: UUID
    video_id: UUID
    video: Optional["VideoRead"] = None
    recommendation_type: str
    slot_position: int
    priority: int
    reason: str
    enabled: bool
    expires_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime
    created_by: UUID

class VideoRecommendationCreate(SQLModel):
    video_id: UUID
    recommendation_type: str
    slot_position: int = 0
    priority: int = 5
    reason: str = ""
    expires_at: Optional[datetime] = None

class VideoRecommendationUpdate(SQLModel):
    recommendation_type: Optional[str] = None
    slot_position: Optional[int] = None
    priority: Optional[int] = None
    reason: Optional[str] = None
    enabled: Optional[bool] = None
    expires_at: Optional[datetime] = None

class RecommendationSlotRead(SQLModel):
    id: int
    slot_name: str
    display_title: str
    description: Optional[str]
    max_items: int
    recommendation_strategy: str
    show_authenticated: bool
    show_unauthenticated: bool
    unauthenticated_strategy: str
    enabled: bool
    created_at: datetime
    updated_at: datetime

class RecommendationSlotCreate(SQLModel):
    slot_name: str
    display_title: str
    description: Optional[str] = None
    max_items: int = 10
    recommendation_strategy: str = "manual_first"
    show_authenticated: bool = True
    show_unauthenticated: bool = True
    unauthenticated_strategy: str = "trending_only"

class RecommendationSlotUpdate(SQLModel):
    display_title: Optional[str] = None
    description: Optional[str] = None
    max_items: Optional[int] = None
    recommendation_strategy: Optional[str] = None
    show_authenticated: Optional[bool] = None
    show_unauthenticated: Optional[bool] = None
    unauthenticated_strategy: Optional[str] = None
    enabled: Optional[bool] = None

class UserVideoScoreRead(SQLModel):
    user_id: UUID
    video_id: UUID
    collaborative_score: float
    similarity_score: float
    category_score: float
    tag_score: float
    final_score: float
    last_updated: datetime

class RecommendationLogRead(SQLModel):
    id: UUID
    user_id: UUID
    video_id: UUID
    recommendation_source: str
    slot_name: str
    impression_rank: int
    clicked: bool
    watched: bool
    clicked_at: Optional[datetime]
    watched_duration: Optional[float]
    created_at: datetime

class RecommendationResponse(SQLModel):
    """推荐API返回的视频推荐"""
    video: VideoRead
    score: float
    source: str
    reason: str

class RecommendationsListResponse(SQLModel):
    """推荐列表API返回"""
    recommendations: List[RecommendationResponse]
    slot_info: dict  # {"slot_name": str, "display_title": str}

# ==================== 转码队列优先级系统 ====================

class TranscodeTask(SQLModel, table=True):
    """转码任务队列"""
    __tablename__ = "transcode_tasks"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    video_id: UUID = Field(foreign_key="videos.id", index=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    status: str = "pending"  # pending, processing, completed, failed, cancelled
    priority: int = 0  # 0-40
    priority_type: str = "normal"  # normal, vip, vip_speedup, paid_speedup
    queue_name: str = "default"  # default, vip, priority, operations
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    waiting_hours: float = 0  # 用于aging计算
    celery_task_id: Optional[str] = None  # Celery任务ID
    worker_name: Optional[str] = None  # 执行任务的worker节点名称
    bump_count: int = 0  # 插队次数
    retry_count: int = 0  # 重试次数（仅允许1次）
    # 暂停相关字段
    pause_percent: float = 0  # 暂停时的进度百分比
    pause_resolution: Optional[str] = None  # 暂停时正在处理的分辨率
    pause_timestamp: Optional[str] = None  # FFmpeg时间戳，用于恢复

    video: Optional["Video"] = Relationship()
    owner: Optional["User"] = Relationship()

class TranscodeTaskRead(SQLModel):
    """转码任务读取"""
    id: UUID
    video_id: UUID
    user_id: UUID
    status: str
    priority: int
    priority_type: str
    queue_name: str
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    waiting_hours: float
    worker_name: Optional[str] = None
    bump_count: int = 0
    retry_count: int = 0
    video_title: Optional[str] = None
    username: Optional[str] = None
    is_vip: bool = False
    pause_percent: float = 0
    pause_resolution: Optional[str] = None
    pause_timestamp: Optional[str] = None

class TranscodeTaskUpdate(SQLModel):
    """转码任务更新"""
    priority: Optional[int] = None
    priority_type: Optional[str] = None
    queue_name: Optional[str] = None
    status: Optional[str] = None


# ==================== 分片上传会话 ====================

class UploadSession(SQLModel, table=True):
    """分片上传会话"""
    __tablename__ = "upload_sessions"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: UUID = Field(foreign_key="users.id", index=True)
    filename: str
    file_size: int = Field(sa_column=Column(BigInteger))  # bytes - 使用 BigInteger 支持 >2GB 文件
    chunk_size: int = 5 * 1024 * 1024  # 5MB per chunk
    total_chunks: int
    uploaded_chunks: Optional[List[int]] = Field(default=None, sa_column=Column(JSON))  # 已上传的分片编号
    temp_dir: str  # 临时分片存储目录
    status: str = "uploading"  # uploading, completed, cancelled
    video_id: Optional[UUID] = None  # 合并后关联的视频ID
    title: Optional[str] = None
    description: Optional[str] = None
    category_id: Optional[int] = None
    visibility: Optional[str] = None
    tags: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    owner: Optional["User"] = Relationship()


class UploadSessionRead(SQLModel):
    """上传会话读取"""
    id: UUID
    user_id: UUID
    filename: str
    file_size: int
    chunk_size: int
    total_chunks: int
    uploaded_chunks: List[int]
    progress: float  # 0-100
    status: str
    video_id: Optional[UUID] = None
    title: Optional[str] = None
    created_at: datetime

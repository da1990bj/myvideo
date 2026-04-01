// Admin Shared Logic

document.addEventListener("DOMContentLoaded", async () => {
    const token = localStorage.getItem("access_token");
    if (!token) return location.href = "/static/login.html";

    // Verify Admin Access
    try {
        const res = await fetch("/users/me", {
            headers: { "Authorization": "Bearer " + token }
        });
        if (res.ok) {
            const user = await res.json();
            if (!user.is_admin) {
                alert("您没有管理员权限");
                location.href = "/static/index.html";
                return;
            }
            renderSidebar();
        } else {
            location.href = "/static/login.html";
        }
    } catch(e) {
        location.href = "/static/index.html";
    }
});

function renderSidebar() {
    const html = `
    <div style="width: 240px; background: #fff; height: 100vh; position: fixed; left: 0; top: 0; border-right: 1px solid #eee; padding: 20px; overflow-y: auto;">
        <h2 style="margin-top:0; color:#00a1d6; margin-bottom:30px;">MyVideo Admin</h2>
        <a href="/static/admin/index.html" class="admin-link">仪表盘</a>
        <a href="/static/admin/transcode.html" class="admin-link">转码队列</a>
        <a href="/static/admin/videos.html" class="admin-link">视频管理</a>
        <a href="/static/admin/recommendations.html" class="admin-link">推荐管理</a>
        <a href="/static/admin/users.html" class="admin-link">用户管理</a>
        <a href="/static/admin/comments.html" class="admin-link">评论管理</a>
        <div style="height: 1px; background: #eee; margin: 10px 0;"></div>
        <a href="/static/admin/roles.html" class="admin-link">角色权限</a>
        <a href="/static/admin/settings.html" class="admin-link">系统设置</a>
        <a href="/static/admin/logs.html" class="admin-link">操作日志</a>
        <div style="height: 1px; background: #eee; margin: 10px 0;"></div>
        <a href="/static/index.html" class="admin-link" style="margin-top:20px; color:#666;">返回前台</a>
    </div>
    <style>
        .admin-link { display: block; padding: 12px; color: #333; text-decoration: none; border-radius: 4px; margin-bottom: 4px; transition: all 0.2s; }
        .admin-link:hover { background: #f4f5f7; color: #00a1d6; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; padding-left: 280px; background-color: #f4f5f7; }
        .container, .content { padding: 20px; max-width: 100%; }
    </style>
    `;
    const div = document.createElement("div");
    div.innerHTML = html;
    document.body.appendChild(div);
}

// 权限映射表 - 中文翻译和说明
const PERMISSIONS_MAP = {
    "admin:super": {
        name: "超级管理员",
        desc: "拥有所有权限，可以管理系统配置和角色权限"
    },
    "video:upload": {
        name: "视频上传",
        desc: "允许用户上传和发布视频"
    },
    "video:audit": {
        name: "视频审核",
        desc: "可以查看待审核视频和系统统计"
    },
    "video:ban": {
        name: "视频下架",
        desc: "可以下架和恢复视频"
    },
    "user:ban": {
        name: "用户封禁",
        desc: "可以封禁和解封用户账户"
    },
    "comment:create": {
        name: "创建评论",
        desc: "允许用户发表和回复评论"
    },
    "comment:delete": {
        name: "删除评论",
        desc: "可以删除用户评论和清理垃圾评论"
    },
    "social:interaction": {
        name: "社交互动",
        desc: "允许关注、点赞和收藏等互动操作"
    }
};

// 将权限代码转换为中文显示
function formatPermissions(permStr) {
    if (!permStr) return "-";
    if (permStr === "*") return "全权限";

    const perms = permStr.split(",").map(p => p.trim()).filter(p => p);
    const names = perms.map(p => PERMISSIONS_MAP[p]?.name || p);
    return names.join(", ");
}

// 角色映射表 - 中文翻译和说明
const ROLES_MAP = {
    "Standard User": {
        name: "普通用户",
        desc: "默认用户角色，拥有基础功能权限"
    },
    "Super Admin": {
        name: "超级管理员",
        desc: "系统管理员，拥有所有权限和配置权限"
    },
    "Content Auditor": {
        name: "内容审核员",
        desc: "可以审核视频、下架视频、删除评论"
    },
    "Operations": {
        name: "运营人员",
        desc: "负责内容运营和推荐管理"
    },
    "User Support": {
        name: "用户支持",
        desc: "处理用户管理和账户相关事务"
    },
    "Muted User": {
        name: "禁言用户",
        desc: "禁止发表评论，但可以上传视频"
    },
    "Restricted User": {
        name: "受限用户",
        desc: "禁止上传视频，但可以发表评论"
    }
};

// 获取角色的中文名称
function getRoleDisplayName(roleName) {
    return ROLES_MAP[roleName]?.name || roleName;
}

// 获取角色的中文描述
function getRoleDisplayDesc(roleName) {
    return ROLES_MAP[roleName]?.desc || "";
}

// 统一导航栏渲染逻辑
const NAV_API_BASE = "";

document.addEventListener("DOMContentLoaded", async () => {
    // 动态加载 CSS
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/css/style.css";
    document.head.appendChild(link);

    // 渲染 HTML 骨架
    const header = document.createElement("header");
    header.className = "global-header";
    header.innerHTML = 
        '<a href="/static/index.html" class="logo">' +
        '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-8 12.5v-9l6 4.5-6 4.5z"/></svg>' +
        ' MyVideo</a>' +
        '<div class="nav-right" id="global-nav-auth"></div>';
    
    // 插入到 body 最前面
    document.body.insertBefore(header, document.body.firstChild);

    // 检查登录状态并更新 UI
    checkNavUser();
});

async function checkNavUser() {
    const token = localStorage.getItem("access_token");
    const area = document.getElementById("global-nav-auth");
    
    if (!token) {
        area.innerHTML = '<a href="/static/login.html" class="login-btn">登录</a>' +
                         '<a href="/static/register.html" class="login-btn" style="background:#00a1d6; color:#fff; padding:6px 16px; border-radius:4px; margin-left:10px;">注册</a>';
        return;
    }

    try {
        const res = await fetch("/users/me", {
            headers: { "Authorization": "Bearer " + token }
        });
        if (res.ok) {
            const user = await res.json();
            const avatar = user.avatar_path || "https://ui-avatars.com/api/?name=" + user.username;

            let adminLink = "";
            if (user.is_admin) {
                adminLink = '<a href="/static/admin/index.html" class="nav-link" style="margin-left:12px; color:#f04c49;">后台管理</a>';
            }

            area.innerHTML =
                '<a href="/static/dashboard.html" class="nav-link">创作中心</a>' +
                '<a href="/static/history.html" class="nav-link" style="margin-left:12px;">历史</a>' +
                adminLink +
                '<div style="display:inline-block; position:relative; margin-left:16px; cursor:pointer; vertical-align:middle;" onclick="location.href=\'/static/notifications.html\'" title="消息通知">' +
                    '<svg width="24" height="24" viewBox="0 0 24 24" fill="#666"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2zm-2 1H8v-6c0-2.48 1.51-4.5 4-4.5s4 2.02 4 4.5v6z"/></svg>' +
                    '<div id="nav-badge" style="display:none; position:absolute; top:-6px; right:-6px; background:#fb7299; color:#fff; font-size:10px; padding:0 4px; border-radius:10px; height:16px; line-height:16px; min-width:16px; text-align:center;">0</div>' +
                '</div>' +
                '<a href="/static/upload.html" class="upload-btn" style="margin-left:16px;">投稿</a>' +
                '<img src="' + avatar + '" class="user-avatar" title="点击进入设置" onclick="location.href=\'/static/settings.html\'" style="margin-left:16px;">' +
                '<a href="#" onclick="logout()" style="font-size:12px; color:#999; margin-left:12px; text-decoration:none;">退出</a>';

            pollNotifications(token);
        } else {
            localStorage.removeItem("access_token");
        }
    } catch(e) { console.error(e); }
}

function pollNotifications(token) {
    const check = async () => {
        try {
            const res = await fetch("/notifications/unread-count", {
                headers: { "Authorization": "Bearer " + token }
            });
            if (res.ok) {
                const data = await res.json();
                const badge = document.getElementById("nav-badge");
                if (badge) {
                    if (data.count > 0) {
                        badge.style.display = "block";
                        badge.innerText = data.count > 99 ? "99+" : data.count;
                    } else {
                        badge.style.display = "none";
                    }
                }
            }
        } catch(e) {}
    };
    check();
    setInterval(check, 30000); // Poll every 30s
}

function logout() {
    localStorage.removeItem("access_token");
    window.location.href = "/static/index.html";
}

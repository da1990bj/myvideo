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
            area.innerHTML =
                '<a href="/static/dashboard.html" class="nav-link">创作中心</a>' +
                '<a href="/static/history.html" class="nav-link" style="margin-left:12px;">历史</a>' + // 新增
                '<a href="/static/upload.html" class="upload-btn" style="margin-left:12px;">投稿</a>' +
                '<img src="' + avatar + '" class="user-avatar" title="点击进入设置" onclick="location.href=\'/static/settings.html\'" style="margin-left:16px;">' +
                '<a href="#" onclick="logout()" style="font-size:12px; color:#999; margin-left:12px; text-decoration:none;">退出</a>';
        } else {
            localStorage.removeItem("access_token");
        }
    } catch(e) { console.error(e); }
}

function logout() {
    localStorage.removeItem("access_token");
    window.location.href = "/static/index.html";
}

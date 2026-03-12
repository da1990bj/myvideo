// 统一导航栏渲染逻辑
const NAV_API_BASE = "";

document.addEventListener("DOMContentLoaded", async () => {
    // 动态加载 CSS
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/static/css/style.css";
    document.head.appendChild(link);

    // Add inline styles for menu items hover effect
    const style = document.createElement('style');
    style.innerHTML = `
        .menu-item:hover { background-color: #f4f5f7; color: #00a1d6 !important; }
    `;
    document.head.appendChild(style);

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
                '<div style="display:inline-block; position:relative; margin-left:12px;" onmouseenter="showHistory(this)" onmouseleave="hideHistory(this)">' +
                    '<a href="/static/history.html" class="nav-link">历史</a>' +
                    '<div class="history-dropdown" style="display:none; position:absolute; top:100%; left:50%; transform:translateX(-50%); width:260px; background:#fff; box-shadow:0 4px 12px rgba(0,0,0,0.15); border-radius:4px; z-index:1000; padding:10px; text-align:left;">' +
                        '<div style="text-align:center; padding:10px; color:#999;">加载中...</div>' +
                    '</div>' +
                '</div>' +
                adminLink +
                '<div style="display:inline-block; position:relative; margin-left:16px; cursor:pointer; vertical-align:middle;" onclick="location.href=\'/static/notifications.html\'" title="消息通知">' +
                    '<svg width="24" height="24" viewBox="0 0 24 24" fill="#666"><path d="M12 22c1.1 0 2-.9 2-2h-4c0 1.1.9 2 2 2zm6-6v-5c0-3.07-1.63-5.64-4.5-6.32V4c0-.83-.67-1.5-1.5-1.5s-1.5.67-1.5 1.5v.68C7.64 5.36 6 7.92 6 11v5l-2 2v1h16v-1l-2-2zm-2 1H8v-6c0-2.48 1.51-4.5 4-4.5s4 2.02 4 4.5v6z"/></svg>' +
                    '<div id="nav-badge" style="display:none; position:absolute; top:-6px; right:-6px; background:#fb7299; color:#fff; font-size:10px; padding:0 4px; border-radius:10px; height:16px; line-height:16px; min-width:16px; text-align:center;">0</div>' +
                '</div>' +

                // User Menu Dropdown
                '<div style="display:inline-block; position:relative; margin-left:16px; vertical-align:middle;" onmouseenter="showUserMenu(this)" onmouseleave="hideUserMenu(this)">' +
                    '<img src="' + avatar + '" class="user-avatar" style="cursor:pointer;">' +
                    '<div class="user-menu-dropdown" style="display:none; position:absolute; top:100%; right:0; width:150px; background:#fff; box-shadow:0 4px 12px rgba(0,0,0,0.15); border-radius:4px; z-index:1000; padding:8px 0; text-align:left;">' +
                        '<div style="padding:10px 16px; font-weight:bold; border-bottom:1px solid #eee; margin-bottom:5px;">' + user.username + '</div>' +
                        '<a href="/static/profile.html?id=' + user.username + '" class="menu-item" style="display:block; padding:8px 16px; color:#333; text-decoration:none; font-size:14px;">个人主页</a>' +
                        '<a href="/static/settings.html" class="menu-item" style="display:block; padding:8px 16px; color:#333; text-decoration:none; font-size:14px;">设置</a>' +
                        '<div style="border-top:1px solid #eee; margin:5px 0;"></div>' +
                        '<a href="#" onclick="logout()" class="menu-item" style="display:block; padding:8px 16px; color:#f04c49; text-decoration:none; font-size:14px;">退出登录</a>' +
                    '</div>' +
                '</div>';


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

// --- History Dropdown Logic ---
function showHistory(el) {
    const dropdown = el.querySelector('.history-dropdown');
    dropdown.style.display = 'block';
    loadNavHistory(dropdown);
}

function hideHistory(el) {
    el.querySelector('.history-dropdown').style.display = 'none';
}

function showUserMenu(el) {
    el.querySelector('.user-menu-dropdown').style.display = 'block';
}

function hideUserMenu(el) {
    el.querySelector('.user-menu-dropdown').style.display = 'none';
}

async function loadNavHistory(container) {
    const token = localStorage.getItem("access_token");
    if (!token) return;

    try {
        const res = await fetch("/users/me/history?size=5", {
             headers: { "Authorization": "Bearer " + token }
        });
        if (res.ok) {
            const videos = await res.json();
            if (videos.length === 0) {
                 container.innerHTML = '<div style="text-align:center; padding:20px; color:#999;">暂无历史记录</div>';
                 return;
            }

            let html = '<div style="font-size:12px; color:#999; padding-bottom:8px; border-bottom:1px solid #eee; margin-bottom:8px;">最近观看</div>';
            videos.forEach(v => {
                const progress = Math.floor(v.progress / 60) + ":" + (Math.floor(v.progress % 60)).toString().padStart(2, '0');
                html += `
                    <a href="/static/video.html?id=${v.id}" style="display:flex; gap:10px; text-decoration:none; color:#333; margin-bottom:10px; align-items:center;">
                         <img src="${v.thumbnail_path || '/static/default_thumb.jpg'}" style="width:80px; height:45px; object-fit:cover; border-radius:4px; background:#eee;">
                         <div style="flex:1; min-width:0;">
                             <div style="font-size:13px; font-weight:500; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-bottom:2px;">${v.title}</div>
                             <div style="font-size:12px; color:#999;">看到 ${progress}</div>
                         </div>
                    </a>
                `;
            });
            html += '<a href="/static/history.html" style="display:block; text-align:center; font-size:12px; color:#00a1d6; text-decoration:none; margin-top:5px; padding-top:5px; border-top:1px solid #eee;">查看全部</a>';
            container.innerHTML = html;
        }
    } catch(e) {
        container.innerHTML = '<div style="text-align:center; color:red;">加载失败</div>';
    }
}

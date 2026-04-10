// 统一导航栏渲染逻辑
const NAV_API_BASE = "";
let siteConfig = { site_name: "MyVideo" }; // 默认值
let notifSocket = null; // WebSocket 连接实例

// 带重试的 fetch
async function fetchWithRetry(url, options = {}, retries = 2) {
    for (let i = 0; i <= retries; i++) {
        try {
            const res = await fetch(url, options);
            return res;
        } catch (e) {
            if (i >= retries) throw e;
            await new Promise(r => setTimeout(r, 200 * (i + 1)));
        }
    }
}

// 生成默认头像（使用首字母，CSS 渲染）
function getDefaultAvatar(username) {
    const initial = username ? username.charAt(0).toUpperCase() : '?';
    const colors = ['#FF6B6B', '#4ECDC4', '#45B7D1', '#96CEB4', '#FFEAA7', '#DDA0DD', '#98D8C8', '#F7DC6F'];
    const color = colors[(username ? username.charCodeAt(0) : 0) % colors.length];
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100"><rect width="100" height="100" fill="${color}" rx="50"/><text x="50" y="50" dy=".35em" text-anchor="middle" fill="white" font-size="40" font-family="Arial">${initial}</text></svg>`;
    return 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
}

// 将 avatar 文件名转换为完整 URL
function getAvatarUrl(avatar_path) {
    if (!avatar_path) return null;
    // 如果已经是完整路径直接返回
    if (avatar_path.startsWith('/')) return avatar_path;
    // 否则拼接 /static/avatars/ 前缀
    return '/static/avatars/' + avatar_path;
}

// 获取或生成匿名设备ID（用于播放统计防刷）
function getAnonymousId() {
    let aid = localStorage.getItem('anonymous_id');
    if (!aid) {
        aid = 'anon_' + Math.random().toString(36).substr(2, 9) + Date.now().toString(36);
        localStorage.setItem('anonymous_id', aid);
    }
    return aid;
}

document.addEventListener("DOMContentLoaded", async () => {
    // 先获取系统配置
    await loadSiteConfig();

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

    // 添加移动端抽屉菜单
    const mobileDrawer = document.createElement('div');
    mobileDrawer.className = 'mobile-nav-drawer';
    mobileDrawer.id = 'mobile-nav-drawer';
    document.body.appendChild(mobileDrawer);

    // 检查是否已有 header 元素（如 index.html 有内联 header）
    const existingHeader = document.querySelector('header');
    if (existingHeader) {
        // 已有 header，只需添加移动端菜单按钮和更新状态
        const mobileMenuBtn = document.createElement('button');
        mobileMenuBtn.className = 'mobile-menu-btn';
        mobileMenuBtn.innerHTML = `
            <svg viewBox="0 0 24 24"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
        `;
        mobileMenuBtn.onclick = toggleMobileNav;
        existingHeader.appendChild(mobileMenuBtn);
        checkNavUser();
        return;
    }

    // 渲染 HTML 骨架，使用动态站点名称
    const header = document.createElement("header");
    header.className = "global-header";
    header.innerHTML =
        '<a href="/static/index.html" class="logo">' +
        '<svg width="24" height="24" viewBox="0 0 24 24" fill="currentColor"><path d="M4 6H2v14c0 1.1.9 2 2 2h14v-2H4V6zm16-4H8c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zm-8 12.5v-9l6 4.5-6 4.5z"/></svg>' +
        ' ' + siteConfig.site_name + '</a>' +
        '<div class="nav-right" id="global-nav-auth"></div>';

    // 添加移动端菜单按钮
    const mobileMenuBtn = document.createElement('button');
    mobileMenuBtn.className = 'mobile-menu-btn';
    mobileMenuBtn.innerHTML = `
        <svg viewBox="0 0 24 24"><path d="M3 18h18v-2H3v2zm0-5h18v-2H3v2zm0-7v2h18V6H3z"/></svg>
    `;
    mobileMenuBtn.onclick = toggleMobileNav;
    header.appendChild(mobileMenuBtn);

    // 插入到 body 最前面
    document.body.insertBefore(header, document.body.firstChild);

    // 检查登录状态并更新 UI
    checkNavUser();
});

// 获取系统配置
async function loadSiteConfig() {
    try {
        const res = await fetchWithRetry("/system/config");
        if (res.ok) {
            const config = await res.json();
            if (config.site_name) {
                siteConfig.site_name = config.site_name;
            }
        }
    } catch(e) {
        console.warn("Failed to load site config:", e);
    }
}

async function checkNavUser() {
    const token = localStorage.getItem("access_token");
    const area = document.getElementById("global-nav-auth");

    if (!token) {
        area.innerHTML = '<a href="/static/login.html" class="login-btn">登录</a>' +
                         '<a href="/static/register.html" class="login-btn" style="background:#00a1d6; color:#fff; padding:6px 16px; border-radius:4px; margin-left:10px;">注册</a>';
        updateMobileNavDrawer(null);
        return;
    }

    try {
        const res = await fetchWithRetry("/users/me", {
            headers: { "Authorization": "Bearer " + token }
        });
        if (res.ok) {
            const user = await res.json();
            console.log('[Nav] User data:', user);
            console.log('[Nav] avatar_path:', user.avatar_path);
            const avatar = getAvatarUrl(user.avatar_path) || getDefaultAvatar(user.username);
            console.log('[Nav] Final avatar:', avatar.substring(0, 50) + '...');

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
                    '<img id="nav-user-avatar" src="' + (getAvatarUrl(user.avatar_path) || getDefaultAvatar(user.username)) + '" class="user-avatar" style="cursor:pointer;">' +
                    '<div class="user-menu-dropdown" style="display:none; position:absolute; top:100%; right:0; width:150px; background:#fff; box-shadow:0 4px 12px rgba(0,0,0,0.15); border-radius:4px; z-index:1000; padding:8px 0; text-align:left;">' +
                        '<div style="padding:10px 16px; font-weight:bold; border-bottom:1px solid #eee; margin-bottom:5px;">' + user.username + '</div>' +
                        '<a href="/static/profile.html?id=' + user.username + '" class="menu-item" style="display:block; padding:8px 16px; color:#333; text-decoration:none; font-size:14px;">个人主页</a>' +
                        '<a href="/static/settings.html" class="menu-item" style="display:block; padding:8px 16px; color:#333; text-decoration:none; font-size:14px;">设置</a>' +
                        '<div style="border-top:1px solid #eee; margin:5px 0;"></div>' +
                        '<a href="#" onclick="logout()" class="menu-item" style="display:block; padding:8px 16px; color:#f04c49; text-decoration:none; font-size:14px;">退出登录</a>' +
                    '</div>' +
                '</div>';


            initNotificationSocket(token);

            // 更新移动端菜单
            updateMobileNavDrawer(user);

            // 设置默认头像（如果 avatar_path 为空或加载失败）
            const navAvatar = document.getElementById("nav-user-avatar");
            if (navAvatar) {
                if (!user.avatar_path) {
                    navAvatar.src = getDefaultAvatar(user.username);
                } else {
                    navAvatar.src = getAvatarUrl(user.avatar_path);
                    navAvatar.onerror = function() {
                        this.src = getDefaultAvatar(user.username);
                        this.onerror = null;
                    };
                }
            }
        } else {
            localStorage.removeItem("access_token");
        }
    } catch(e) { console.error(e); }
}

function initNotificationSocket(token) {
    // 初始化时先获取一次未读数
    const check = async () => {
        try {
            const res = await fetchWithRetry("/notifications/unread-count", {
                headers: { "Authorization": "Bearer " + token }
            });
            if (res.ok) {
                const data = await res.json();
                updateBadge(data.count);
            }
        } catch(e) {}
    };
    check();

    // 避免重复连接
    if (notifSocket) {
        notifSocket.disconnect();
    }

    const socketOptions = {
        auth: { token: token },
        reconnection: true,
        reconnectionDelay: 1000,
        reconnectionDelayMax: 5000,
        reconnectionAttempts: 10,
        transports: ['websocket', 'polling']
    };

    const connectSocket = () => {
        try {
            notifSocket = io('/', socketOptions);

            notifSocket.on('connect', () => {
                console.log('✅ Notification socket connected:', notifSocket.id);
            });

            notifSocket.on('notification_count', (data) => {
                updateBadge(data.count);
            });

            notifSocket.on('disconnect', (reason) => {
                console.log('Notification socket disconnected:', reason);
            });

            notifSocket.on('error', (error) => {
                console.error('Notification socket error:', error);
            });

        } catch (e) {
            console.error('Failed to initialize notification socket:', e);
        }
    };

    // 如果 Socket.IO 未加载，先动态加载
    if (typeof io === 'undefined') {
        const script = document.createElement('script');
        script.src = '/static/js/socket.io.js';
        script.onload = connectSocket;
        script.onerror = () => console.error('Failed to load Socket.IO');
        document.head.appendChild(script);
    } else {
        connectSocket();
    }
}

function updateBadge(count) {
    const badge = document.getElementById("nav-badge");
    if (badge) {
        if (count > 0) {
            badge.style.display = "block";
            badge.innerText = count > 99 ? "99+" : count;
        } else {
            badge.style.display = "none";
        }
    }
}

function logout() {
    localStorage.removeItem("access_token");
    window.location.href = "/static/index.html";
}

// 移动端菜单切换
function toggleMobileNav() {
    const drawer = document.getElementById('mobile-nav-drawer');
    drawer.classList.toggle('open');
    // 点击其他区域关闭
    if (drawer.classList.contains('open')) {
        document.addEventListener('click', closeMobileNavOnClickOutside);
    }
}

function closeMobileNavOnClickOutside(e) {
    const drawer = document.getElementById('mobile-nav-drawer');
    const menuBtn = document.querySelector('.mobile-menu-btn');
    if (!drawer.contains(e.target) && !menuBtn.contains(e.target)) {
        drawer.classList.remove('open');
        document.removeEventListener('click', closeMobileNavOnClickOutside);
    }
}

// 更新移动端菜单内容
function updateMobileNavDrawer(user = null) {
    const drawer = document.getElementById('mobile-nav-drawer');
    if (!drawer) return;

    if (!user) {
        drawer.innerHTML = `
            <a href="/static/login.html">登录</a>
            <a href="/static/register.html">注册</a>
        `;
    } else {
        const avatar = getAvatarUrl(user.avatar_path) || getDefaultAvatar(user.username);
        let adminLink = user.is_admin ? `<a href="/static/admin/index.html">后台管理</a>` : '';

        drawer.innerHTML = `
            <div class="mobile-nav-user">
                <div style="display:flex; align-items:center; gap:12px;">
                    <img src="${avatar}" class="avatar" onerror="this.src='${getDefaultAvatar(user.username)}'">
                    <div>
                        <div class="username">${user.username}</div>
                        <a href="/static/profile.html?id=${user.username}" style="font-size:13px; color:#00a1d6; text-decoration:none;">查看主页</a>
                    </div>
                </div>
            </div>
            <a href="/static/index.html">首页</a>
            <a href="/static/dashboard.html">创作中心</a>
            <a href="/static/history.html">观看历史</a>
            <a href="/static/notifications.html">消息通知</a>
            <a href="/static/collection_manage.html">我的收藏</a>
            <div class="nav-divider"></div>
            ${adminLink}
            <a href="/static/settings.html">设置</a>
            <a href="#" onclick="logout(); return false;" style="color:#f04c49;">退出登录</a>
        `;
    }
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
        const res = await fetchWithRetry("/users/me/history?size=5", {
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

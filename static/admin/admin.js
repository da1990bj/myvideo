// Admin Shared Logic

// 默认菜单配置
const DEFAULT_MENU = [
    { id: 'index', href: '/static/admin/index.html', text: '仪表盘' },
    { id: 'transcode', href: '/static/admin/transcode.html', text: '转码队列' },
    { id: 'videos', href: '/static/admin/videos.html', text: '视频管理' },
    { id: 'categories', href: '/static/admin/categories.html', text: '分类管理' },
    { id: 'recommendations', href: '/static/admin/recommendations.html', text: '推荐管理' },
    { id: 'users', href: '/static/admin/users.html', text: '用户管理' },
    { id: 'comments', href: '/static/admin/comments.html', text: '评论管理' },
    { id: 'divider1', type: 'divider' },
    { id: 'roles', href: '/static/admin/roles.html', text: '角色权限' },
    { id: 'settings', href: '/static/admin/settings.html', text: '系统设置' },
    { id: 'logs', href: '/static/admin/logs.html', text: '操作日志' },
    { id: 'divider2', type: 'divider' },
    { id: 'back', href: '/static/index.html', text: '返回前台', style: 'margin-top:20px; color:#666;' },
];

let currentMenu = [...DEFAULT_MENU];
let apiMenuLoaded = false;
let sidebarCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';

// 切换侧边栏显示/隐藏
function toggleSidebar() {
    sidebarCollapsed = !sidebarCollapsed;
    localStorage.setItem('sidebarCollapsed', sidebarCollapsed);
    updateSidebarState();
}

// 更新侧边栏状态
function updateSidebarState() {
    const sidebar = document.getElementById('admin-sidebar');
    const toggleBtn = document.getElementById('sidebar-toggle-btn');
    const floatBtn = document.getElementById('sidebar-float-btn');
    if (!sidebar) return;

    if (sidebarCollapsed) {
        sidebar.style.left = '-300px';
        sidebar.style.boxShadow = 'none';
        sidebar.style.borderRight = 'none';
        document.body.classList.add('sidebar-collapsed');
        if (toggleBtn) toggleBtn.style.display = 'none';
        if (floatBtn) floatBtn.style.display = 'block';
    } else {
        sidebar.style.left = '0';
        sidebar.style.boxShadow = '2px 0 10px rgba(0,0,0,0.1)';
        sidebar.style.borderRight = '1px solid #eee';
        document.body.classList.remove('sidebar-collapsed');
        if (toggleBtn) toggleBtn.style.display = 'block';
        if (floatBtn) floatBtn.style.display = 'none';
    }
}

// 从 API 加载菜单顺序
async function loadMenuOrderFromAPI() {
    try {
        const token = localStorage.getItem('access_token');
        console.log('[Admin] Fetching menu order from API...');
        const res = await fetch('/admin/menu-order', {
            headers: { 'Authorization': 'Bearer ' + token }
        });
        console.log('[Admin] Menu order API response:', res.status);
        if (res.ok) {
            const data = await res.json();
            console.log('[Admin] Menu order data:', data);
            if (data.order && Array.isArray(data.order)) {
                // 合并：使用保存的顺序，但保留新增的菜单项
                const savedIds = data.order.map(item => item.id);
                const newItems = DEFAULT_MENU.filter(item => !savedIds.includes(item.id));
                currentMenu = [...data.order, ...newItems];
                apiMenuLoaded = true;
                console.log('[Admin] Menu order updated from API');
            } else {
                console.log('[Admin] No saved menu order, using default');
            }
        }
    } catch (e) {
        console.warn('[Admin] Failed to load menu order from API:', e);
    }
    return currentMenu;
}

// 保存菜单顺序到 API
async function saveMenuOrderToAPI(menu) {
    try {
        const token = localStorage.getItem('access_token');
        const res = await fetch('/admin/menu-order', {
            method: 'PUT',
            headers: {
                'Authorization': 'Bearer ' + token,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify(menu)
        });
        if (!res.ok) {
            console.warn('Failed to save menu order to API');
        }
    } catch (e) {
        console.warn('Failed to save menu order to API:', e);
    }
}

// 渲染单个菜单项
function renderMenuItem(item) {
    if (item.type === 'divider') {
        return '<div style="height: 1px; background: #eee; margin: 10px 0;"></div>';
    }
    const isActive = window.location.pathname === item.href;
    const activeStyle = isActive ? 'background: #e3f2fd; color: #00a1d6; font-weight: 500;' : '';
    const dragHandle = '<span class="drag-handle" style="float:left; opacity:0.3; cursor:grab; margin-right:8px;">&#9776;</span>';
    return `
        <a href="${item.href}"
           class="admin-link"
           data-id="${item.id}"
           draggable="true"
           style="${activeStyle}${item.style || ''}">
            ${dragHandle}${item.text}
        </a>
    `;
}

// 渲染侧边栏
function renderSidebar(menu) {
    const menuHtml = menu.map(renderMenuItem).join('');

    const html = `
    <div id="admin-sidebar">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
            <h2 style="margin:0; color:#00a1d6;">MyVideo Admin</h2>
            <button id="sidebar-toggle-btn" onclick="toggleSidebar()" style="background:none; border:none; cursor:pointer; font-size:20px; color:#999; padding:4px;" title="收起菜单">&#9776;</button>
        </div>
        <div id="menu-container">
            ${menuHtml}
        </div>
    </div>
    <div id="sidebar-float-btn" onclick="toggleSidebar()" style="display:none; position:fixed; left:10px; top:20px; z-index:99; background:#00a1d6; color:#fff; width:40px; height:40px; border-radius:50%; cursor:pointer; font-size:18px; text-align:center; line-height:40px; box-shadow:0 2px 10px rgba(0,0,0,0.2);" title="展开菜单">&#9776;</div>
    <style>
        #admin-sidebar { width: 240px; background: #fff; height: 100vh; position: fixed; left: 0; top: 0; border-right: 1px solid #eee; padding: 20px; overflow-y: auto; z-index: 100; transition: left 0.3s, box-shadow 0.3s; }
        .admin-link { display: block; padding: 12px; color: #333; text-decoration: none; border-radius: 4px; margin-bottom: 4px; transition: all 0.2s; cursor: pointer; user-select: none; }
        .admin-link:hover { background: #f4f5f7; color: #00a1d6; }
        .admin-link.dragging { opacity: 0.5; background: #e3f2fd; }
        .admin-link.drag-over { border-top: 2px solid #00a1d6; }
        .admin-link.drag-over-next { border-bottom: 2px solid #00a1d6; }
        .drag-handle:hover { opacity: 1 !important; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0 !important; padding-left: 280px !important; background-color: #f4f5f7 !important; transition: padding-left 0.3s; }
        body.sidebar-collapsed { padding-left: 20px !important; }
        .container, .content { padding: 20px; max-width: 100%; }
        #menu-container { min-height: 50px; }
        #sidebar-toggle-btn:hover { color: #00a1d6; }
    </style>
    `;

    const div = document.createElement("div");
    div.innerHTML = html;
    document.body.appendChild(div);

    // 应用保存的状态
    if (sidebarCollapsed) {
        document.body.classList.add('sidebar-collapsed');
    }
    updateSidebarState();

    initDragAndDrop();
}

// 获取当前菜单顺序
function getCurrentMenuOrder() {
    const container = document.getElementById('menu-container');
    const items = container.querySelectorAll('.admin-link');
    const menu = [];

    const defaultMenuMap = {};
    DEFAULT_MENU.forEach(item => defaultMenuMap[item.id] = item);

    items.forEach(item => {
        const id = item.dataset.id;
        if (id && defaultMenuMap[id]) {
            menu.push(defaultMenuMap[id]);
        }
    });

    return menu;
}

// 初始化拖拽功能
function initDragAndDrop() {
    const container = document.getElementById('menu-container');
    const items = container.querySelectorAll('.admin-link');

    let draggedItem = null;

    items.forEach(item => {
        // 拖拽开始
        item.addEventListener('dragstart', (e) => {
            draggedItem = item;
            item.classList.add('dragging');
            e.dataTransfer.effectAllowed = 'move';
            e.dataTransfer.setData('text/plain', item.dataset.id);
        });

        // 拖拽结束
        item.addEventListener('dragend', () => {
            item.classList.remove('dragging');
            items.forEach(i => {
                i.classList.remove('drag-over');
                i.classList.remove('drag-over-next');
            });
            draggedItem = null;

            // 保存新顺序
            const menu = getCurrentMenuOrder();
            currentMenu = menu;
            saveMenuOrderToAPI(menu);
        });

        // 拖拽经过
        item.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.dataTransfer.dropEffect = 'move';

            if (item === draggedItem) return;

            const rect = item.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;

            items.forEach(i => i.classList.remove('drag-over', 'drag-over-next'));

            if (e.clientY < midY) {
                item.classList.add('drag-over');
            } else {
                item.classList.add('drag-over-next');
            }
        });

        // 拖拽离开
        item.addEventListener('dragleave', () => {
            item.classList.remove('drag-over', 'drag-over-next');
        });

        // 放置
        item.addEventListener('drop', (e) => {
            e.preventDefault();

            if (item === draggedItem) return;

            const rect = item.getBoundingClientRect();
            const midY = rect.top + rect.height / 2;
            const insertBefore = e.clientY < midY;

            draggedItem.remove();

            if (insertBefore) {
                container.insertBefore(draggedItem, item);
            } else {
                container.insertBefore(draggedItem, item.nextSibling);
            }
        });
    });
}

// 初始化
async function initAdmin() {
    console.log('[Admin] Initializing admin panel...');
    const token = localStorage.getItem("access_token");
    if (!token) {
        console.log('[Admin] No token found, redirecting to login');
        location.href = "/static/login.html";
        return;
    }

    try {
        const res = await fetch("/users/me", {
            headers: { "Authorization": "Bearer " + token }
        });
        console.log('[Admin] /users/me response:', res.status);
        if (res.ok) {
            const user = await res.json();
            console.log('[Admin] User:', user.username, 'is_admin:', user.is_admin);
            if (!user.is_admin) {
                alert("您没有管理员权限");
                location.href = "/static/index.html";
                return;
            }

            // 先加载菜单顺序，再渲染侧边栏
            console.log('[Admin] Loading menu order...');
            await loadMenuOrderFromAPI();
            console.log('[Admin] Current menu:', currentMenu.length, 'items');
            renderSidebar(currentMenu);
            console.log('[Admin] Sidebar rendered successfully');
        } else {
            console.log('[Admin] Not ok response, redirecting to login');
            location.href = "/static/login.html";
        }
    } catch(e) {
        console.error('[Admin] Error:', e);
        location.href = "/static/login.html";
    }
}

document.addEventListener("DOMContentLoaded", initAdmin);

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

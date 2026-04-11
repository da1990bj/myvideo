// Admin Shared Logic

// 默认菜单配置
const DEFAULT_MENU = [
    { id: 'index', href: '/static/admin/index.html', text: '仪表盘' },
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

// 注入统一全局样式
function injectCommonStyles() {
    if (document.getElementById('admin-common-styles')) return;
    const style = document.createElement('style');
    style.id = 'admin-common-styles';
    style.textContent = `
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 0; padding: 0; background: #f5f5f5; }
        .container { padding: 20px; max-width: 1600px; margin: 0 auto; }
        h1 { margin-bottom: 20px; color: #333; }
        h2 { margin-bottom: 15px; color: #333; }
        h3 { margin-top: 30px; margin-bottom: 15px; color: #333; }

        /* 统计卡片 */
        .stats-row, .stats-grid, .stats { display: flex; gap: 15px; flex-wrap: wrap; margin-bottom: 20px; align-items: center; }
        .stat-card, .card {
            background: #fff; padding: 20px 30px; border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.08); text-align: center; flex: 1;
            min-width: var(--card-min-width, 120px); max-width: var(--card-max-width, 300px);
        }
        .stat-card .label, .card .label { color: #888; font-size: 13px; margin-bottom: 8px; }
        .stat-card .num, .card .num { font-size: 28px; font-weight: bold; color: #00a1d6; }
        .stat-card .stat-value { font-size: 24px; font-weight: 600; color: #00a1d6; margin-bottom: 4px; }
        .stat-card .stat-label, .card .stat-label { font-size: 12px; color: #999; }

        /* 表格样式 */
        table { width: 100%; border-collapse: collapse; background: #fff; box-shadow: 0 2px 8px rgba(0,0,0,0.08); border-radius: 8px; overflow: hidden; }
        th, td { padding: 12px 15px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #fafafa; font-weight: 500; color: #555; }
        tr:last-child td { border-bottom: none; }
        tr:hover { background: #f9f9f9; }

        /* 状态标签 */
        .status-badge { display: inline-block; padding: 3px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }
        .status-pending { background: #fff3e0; color: #f08c00; }
        .status-processing { background: #e3f2fd; color: #00a1d6; }
        .status-completed { background: #e8f5e9; color: #2f9e44; }
        .status-failed { background: #ffebee; color: #f04c49; }
        .status-cancelled { background: #f5f5f5; color: #999; }
        .status-paused { background: #fff8e1; color: #f08c00; }
        .audit-pending { background: #fff3e0; color: #f08c00; }
        .audit-approved { background: #e8f5e9; color: #2f9e44; }
        .audit-banned { background: #ffebee; color: #f04c49; }
        .audit-appealing { background: #fff8e1; color: #e65100; }

        /* 按钮 */
        .btn { padding: 5px 12px; border-radius: 4px; border: 1px solid #ddd; cursor: pointer; background: #fff; font-size: 12px; transition: all 0.2s; text-decoration: none; display: inline-block; }
        .btn:hover { background: #f5f5f5; }
        .btn-danger { color: #f04c49; border-color: #f04c49; }
        .btn-danger:hover { background: #ffebee; }
        .btn-success { color: #00a1d6; border-color: #00a1d6; }
        .btn-success:hover { background: #e3f2fd; }
        .btn-warning { color: #f08c00; border-color: #f08c00; }
        .btn-warning:hover { background: #fff3e0; }
        .btn-purple { color: #9c27b0; border-color: #9c27b0; }
        .btn-purple:hover { background: #f3e5f5; }
        .btn-small { padding: 3px 8px; font-size: 11px; }
        .btn-secondary { color: #666; border-color: #ddd; }

        /* 刷新提示 */
        .refresh-info { color: #999; font-size: 12px; margin-bottom: 15px; }
        .refresh-info button { padding: 4px 10px; margin-left: 10px; background: #00a1d6; color: #fff; border: none; border-radius: 4px; cursor: pointer; font-size: 12px; }
        .refresh-info button:hover { background: #008dbd; }

        /* 分隔线 */
        .divider { border-left: 1px solid #ddd; height: 24px; margin: 0 4px; }

        /* 角色标签 */
        .role-tag { display: inline-block; padding: 2px 8px; border-radius: 12px; font-size: 12px; margin: 2px; background: #e7f5ff; color: #00a1d6; }

        /* 缩略图 */
        .thumb { width: 80px; height: 45px; object-fit: cover; border-radius: 4px; background: #eee; }

        /* 操作区域 */
        .op-group { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
        .op-label { font-size: 11px; color: #999; margin-right: 4px; }

        /* 进度显示 */
        .progress-cell { font-weight: 500; color: #00a1d6; }
        .progress-cell.completed { color: #2f9e44; }
        .progress-cell.failed { color: #f04c49; }
    `;
    document.head.appendChild(style);
}

// 渲染侧边栏
function renderSidebar(menu) {
    const menuHtml = menu.map(renderMenuItem).join('');
    const savedWidth = localStorage.getItem('sidebarWidth') || 240;

    const html = `
    <div id="admin-sidebar">
        <div id="sidebar-inner" style="padding: 20px; height: 100%; box-sizing: border-box; overflow-y: auto;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
                <h2 style="margin:0; color:#00a1d6;">MyVideo Admin</h2>
                <button id="sidebar-toggle-btn" onclick="toggleSidebar()" style="background:none; border:none; cursor:pointer; font-size:20px; color:#999; padding:4px;" title="收起菜单">&#9776;</button>
            </div>
            <div id="menu-container">
                ${menuHtml}
            </div>
        </div>
    </div>
    <div id="sidebar-resize-handle" style="position: fixed; left: ${parseInt(savedWidth)}px; top: 0; width: 8px; height: 100vh; cursor: ew-resize; z-index: 101; background: transparent;"></div>
    <div id="sidebar-float-btn" onclick="toggleSidebar()" style="display:none; position:fixed; left:10px; top:20px; z-index:99; background:#00a1d6; color:#fff; width:40px; height:40px; border-radius:50%; cursor:pointer; font-size:18px; text-align:center; line-height:40px; box-shadow:0 2px 10px rgba(0,0,0,0.2);" title="展开菜单">&#9776;</div>
    <div id="admin-toolbar" style="position: fixed; top: 10px; right: 20px; z-index: 90; display: flex; gap: 15px; align-items: center;">
        <div id="card-width-control" style="display:none; align-items: center; gap: 8px;">
            <span style="color: #666; font-size: 12px;">卡片宽度：</span>
            <input type="range" id="card-width-slider" min="100" max="300" value="160" style="width: 100px; vertical-align: middle;">
            <span id="card-width-label" style="color: #666; font-size: 12px;">160px</span>
        </div>
    </div>
    <style>
        #admin-sidebar { width: ${savedWidth}px; background: #fff; height: 100vh; position: fixed; left: 0; top: 0; border-right: 1px solid #eee; overflow: hidden; z-index: 100; transition: width 0s; }
        #sidebar-inner { padding: 20px; }
        #sidebar-resize-handle:hover { background: rgba(0, 160, 214, 0.2); }
        #sidebar-resize-handle.resizing { background: rgba(0, 160, 214, 0.3); }
        .admin-link { display: block; padding: 12px; color: #333; text-decoration: none; border-radius: 4px; margin-bottom: 4px; transition: all 0.2s; cursor: pointer; user-select: none; }
        .admin-link:hover { background: #f4f5f7; color: #00a1d6; }
        .admin-link.dragging { opacity: 0.5; background: #e3f2fd; }
        .admin-link.drag-over { border-top: 2px solid #00a1d6; }
        .admin-link.drag-over-next { border-bottom: 2px solid #00a1d6; }
        .drag-handle:hover { opacity: 1 !important; }
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0 !important; padding-left: var(--sidebar-width, ${parseInt(savedWidth) + 40}px) !important; background-color: #f4f5f7 !important; transition: padding-left 0.3s; }
        body.sidebar-collapsed { padding-left: 20px !important; }
        .container, .content { padding: 20px; max-width: 100%; }
        #menu-container { min-height: 50px; }
        #sidebar-toggle-btn:hover { color: #00a1d6; }
        .stats-row { --card-min-width: 160px; --card-max-width: 320px; }
        .stats-grid { --card-min-width: 160px; --card-max-width: 320px; }
        .stats { --card-min-width: 160px; --card-max-width: 320px; }
    </style>
    `;

    const div = document.createElement("div");
    div.innerHTML = html;
    document.body.appendChild(div);

    // 注入统一样式到 document head
    injectCommonStyles();

    // 应用保存的状态
    if (sidebarCollapsed) {
        document.body.classList.add('sidebar-collapsed');
    }
    updateSidebarState();

    initDragAndDrop();
    initSidebarResize();
}

// 初始化侧边栏宽度拖拽
function initSidebarResize() {
    const defaultWidth = 240;
    const savedWidth = parseInt(localStorage.getItem('sidebarWidth')) || defaultWidth;
    const sidebar = document.getElementById('admin-sidebar');
    const handle = document.getElementById('sidebar-resize-handle');
    if (!sidebar || !handle) return;

    // 重置为默认宽度
    sidebar.style.width = defaultWidth + 'px';
    handle.style.left = defaultWidth + 'px';
    document.body.style.setProperty('--sidebar-width', (defaultWidth + 40) + 'px');
    localStorage.setItem('sidebarWidth', defaultWidth);

    let isResizing = false;
    let startX = 0;
    let startWidth = 0;

    handle.addEventListener('mousedown', (e) => {
        isResizing = true;
        startX = e.clientX;
        startWidth = parseInt(sidebar.style.width) || defaultWidth;
        handle.classList.add('resizing');
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!isResizing) return;
        const diff = e.clientX - startX;
        const newWidth = Math.max(150, Math.min(500, startWidth + diff));
        sidebar.style.width = newWidth + 'px';
        handle.style.left = newWidth + 'px';
        document.body.style.setProperty('--sidebar-width', (newWidth + 40) + 'px');
    });

    document.addEventListener('mouseup', () => {
        if (!isResizing) return;
        isResizing = false;
        handle.classList.remove('resizing');
        const finalWidth = parseInt(sidebar.style.width) || defaultWidth;
        localStorage.setItem('sidebarWidth', finalWidth);
    });
}

// ==================== 卡片宽度调整 ====================
function updateCardWidth(value) {
    // videos.html - .stats-row with CSS variables
    document.querySelectorAll('.stats-row').forEach(row => {
        row.style.setProperty('--card-min-width', value + 'px');
        row.style.setProperty('--card-max-width', (value * 2) + 'px');
    });
    // settings.html, recommendations.html - .stats-grid / .stats with CSS variables
    document.querySelectorAll('.stats-grid, .stats').forEach(grid => {
        grid.style.setProperty('--card-min-width', value + 'px');
        grid.style.setProperty('--card-max-width', (value * 2) + 'px');
    });
    // index.html - set card width directly (cards use flex: 0 0 <width>)
    document.querySelectorAll('.card').forEach(card => {
        card.style.flex = `0 0 ${value}px`;
        card.style.minWidth = value + 'px';
        card.style.maxWidth = (value * 2) + 'px';
    });
    const label = document.getElementById('card-width-label');
    if (label) label.textContent = value + 'px';
    localStorage.setItem('statCardWidth', value);
}

function initCardWidthControl() {
    const control = document.getElementById('card-width-control');
    const slider = document.getElementById('card-width-slider');
    if (!control || !slider) return;

    // 检查页面上是否有任何卡片容器
    const hasCards = document.querySelector('.stats-row, .stats-grid, .stats, .card') !== null;
    if (hasCards) {
        control.style.display = 'flex';
        // 恢复保存的宽度
        const saved = localStorage.getItem('statCardWidth');
        if (saved) {
            slider.value = saved;
            updateCardWidth(saved);
        }
        slider.addEventListener('input', (e) => updateCardWidth(e.target.value));
    }
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
            initCardWidthControl();
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

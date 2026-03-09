// 后端 API 地址
const API_BASE = "";

// 封装带 Token 的请求
async function authFetch(url, options = {}) {
    const token = localStorage.getItem("access_token");
    
    // 如果需要 Token 且没有 Token
    // GET 请求通常允许匿名访问，POST/PUT 等通常需要登录
    const headers = options.headers || {};
    if (token) {
        headers["Authorization"] = "Bearer " + token;
    }

    try {
        const response = await fetch(API_BASE + url, {
            ...options,
            headers: {
                "Content-Type": "application/json",
                ...headers
            }
        });

        if (response.status === 401) {
            // Token 过期或无效
            localStorage.removeItem("access_token");
            return null; 
        }

        return response;
    } catch (e) {
        console.error("Network error:", e);
        return null;
    }
}

// 核心函数: 检查用户状态 (只返回数据，不操作 UI)
async function checkUserStatus() {
    const token = localStorage.getItem("access_token");
    if (!token) return null;

    const res = await authFetch("/users/me");
    if (res && res.ok) {
        return await res.json();
    }
    // 如果 Token 无效，authFetch 会返回 null 或 401，这里统一返回 null
    return null;
}

// 登出
function logout() {
    localStorage.removeItem("access_token");
    window.location.href = "/static/index.html";
}

// 格式化时间
function formatTime(dateString) {
    const date = new Date(dateString);
    return date.toLocaleDateString();
}

// 加载分类到下拉框 (给 upload.html 用)
async function loadCategoriesToSelect(selectId) {
    try {
        const res = await fetch("/categories");
        if (res.ok) {
            const categories = await res.json();
            const select = document.getElementById(selectId);
            if (!select) return;

            select.innerHTML = '<option value="" disabled selected>选择分类...</option>';
            categories.forEach(cat => {
                const option = document.createElement("option");
                option.value = cat.id;
                option.textContent = cat.name;
                select.appendChild(option);
            });
        }
    } catch (e) {
        console.error("Failed to load categories:", e);
    }
}

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
    <div style="width: 240px; background: #fff; height: 100vh; position: fixed; left: 0; top: 0; border-right: 1px solid #eee; padding: 20px;">
        <h2 style="margin-top:0; color:#00a1d6; margin-bottom:30px;">MyVideo Admin</h2>
        <a href="/static/admin/index.html" class="admin-link">仪表盘</a>
        <a href="/static/admin/videos.html" class="admin-link">视频管理</a>
        <a href="/static/admin/users.html" class="admin-link">用户管理</a>
        <a href="/static/admin/comments.html" class="admin-link">评论管理</a>
        <a href="/static/index.html" class="admin-link" style="margin-top:20px; color:#666;">返回前台</a>
    </div>
    <style>
        .admin-link { display: block; padding: 12px; color: #333; text-decoration: none; border-radius: 4px; margin-bottom: 4px; }
        .admin-link:hover { background: #f4f5f7; color: #00a1d6; }
        body { font-family: sans-serif; margin: 0; }
    </style>
    `;
    const div = document.createElement("div");
    div.innerHTML = html;
    document.body.appendChild(div);
    document.body.style.paddingLeft = "260px";
    document.body.style.backgroundColor = "#f4f5f7";
}

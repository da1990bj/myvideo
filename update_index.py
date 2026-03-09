
html_content = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MyVideo - 首页</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; margin: 0; padding: 0; background: #f9f9f9; color: #333; }
        
        /* 顶部导航 */
        header { background: #fff; padding: 0 20px; height: 64px; display: flex; align-items: center; box-shadow: 0 1px 4px rgba(0,0,0,0.05); position: sticky; top: 0; z-index: 100; }
        .logo { font-size: 22px; font-weight: 800; color: #fb7299; text-decoration: none; margin-right: 40px; }
        
        .nav-links { display: flex; gap: 20px; overflow-x: auto; white-space: nowrap; scrollbar-width: none; }
        .nav-links::-webkit-scrollbar { display: none; }
        .nav-links a { text-decoration: none; color: #555; font-size: 15px; font-weight: 500; transition: color 0.2s; padding: 5px 0; }
        .nav-links a:hover, .nav-links a.active { color: #fb7299; border-bottom: 2px solid #fb7299; }
        
        .search-box { flex: 1; max-width: 400px; margin: 0 20px; display: flex; align-items: center; background: #f4f4f4; border-radius: 20px; padding: 5px 15px; }
        .search-box input { border: none; background: transparent; outline: none; width: 100%; font-size: 14px; }
        
        .user-area { margin-left: auto; display: flex; gap: 15px; align-items: center; font-size: 14px; }
        .btn-upload { background: #fb7299; color: #fff; padding: 6px 16px; border-radius: 4px; text-decoration: none; font-weight: 600; transition: opacity 0.2s; }
        .btn-upload:hover { opacity: 0.9; }
        
        /* 主要内容 */
        .container { max-width: 1400px; margin: 20px auto; padding: 0 20px; }
        
        /* 网格布局 */
        .grid-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .grid-header h2 { margin: 0; font-size: 20px; font-weight: 600; }
        
        .video-grid { 
            display: grid; 
            grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); 
            gap: 20px 15px; 
        }
        
        .video-card { cursor: pointer; transition: transform 0.2s; background: transparent; }
        .video-card:hover { transform: translateY(-4px); }
        
        .thumbnail-box { 
            position: relative; 
            width: 100%; 
            padding-top: 56.25%; /* 16:9 */ 
            background: #e7e7e7; 
            border-radius: 6px; 
            overflow: hidden; 
            margin-bottom: 8px;
        }
        .thumbnail-box img { 
            position: absolute; 
            top: 0; left: 0; width: 100%; height: 100%; 
            object-fit: cover; 
        }
        .duration { 
            position: absolute; bottom: 6px; right: 6px; 
            background: rgba(0,0,0,0.6); color: #fff; 
            font-size: 12px; padding: 2px 6px; border-radius: 4px; 
        }
        
        .info { padding: 0 4px; }
        .title { 
            font-size: 15px; font-weight: 500; line-height: 22px; color: #222; 
            margin-bottom: 4px;
            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; 
            height: 44px;
        }
        .meta { font-size: 13px; color: #999; display: flex; align-items: center; gap: 10px; }
        .author { display: flex; align-items: center; gap: 5px; }
        .author:hover { color: #fb7299; }
        
    </style>
</head>
<body>

<header>
    <a href="/" class="logo">MyVideo</a>
    
    <nav class="nav-links" id="category-nav">
        <a href="#" class="active" onclick="switchCategory(null, this)">首页</a>
        <!-- 动态加载分类 -->
    </nav>
    
    <div class="search-box">
        <input type="text" placeholder="搜索视频..." onkeypress="handleSearch(event)">
    </div>

    <div class="user-area" id="user-area">
        <a href="/static/login.html" style="color:#fb7299;">登录</a>
        <a href="/static/register.html">注册</a>
    </div>
</header>

<div class="container">
    <div class="grid-header">
        <h2 id="section-title">🔥 热门推荐</h2>
    </div>
    
    <div class="video-grid" id="video-grid">
        <!-- 视频列表动态加载 -->
        <div style="grid-column: 1/-1; text-align: center; color: #999; padding: 40px;">
            加载中...
        </div>
    </div>
</div>

<script src="/static/js/app.js"></script>
<script>
    let currentCategoryId = null;

    document.addEventListener("DOMContentLoaded", async () => {
        await initUser();
        await initCategories();
        loadVideos();
    });

    // 初始化用户状态
    async function initUser() {
        const token = localStorage.getItem("access_token");
        if (token) {
            try {
                // 直接调用原生 fetch 避免跳转 loop
                const res = await fetch("/users/me", {
                    headers: { "Authorization": "Bearer " + token }
                });
                if (res.ok) {
                    const user = await res.json();
                    document.getElementById("user-area").innerHTML = `
                        <a href="/static/upload.html" class="btn-upload">☁️ 投稿</a>
                        <div style="cursor:pointer;" onclick="location.href='/static/profile.html'">
                            <img src="${user.avatar_path || '/static/default_avatar.png'}" 
                                 style="width:32px; height:32px; border-radius:50%; vertical-align:middle; border:1px solid #ddd;">
                        </div>
                        <span style="max-width:80px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">${user.username}</span>
                        <a href="#" onclick="logout()" style="color:#999; font-size:12px;">[退]</a>
                    `;
                } else {
                    localStorage.removeItem("access_token"); // Token 失效清理
                }
            } catch (e) { console.error(e); }
        }
    }

    // 加载分类
    async function initCategories() {
        try {
            const res = await fetch("/categories");
            if (res.ok) {
                const categories = await res.json();
                const nav = document.getElementById("category-nav");
                categories.forEach(cat => {
                    const a = document.createElement("a");
                    a.href = "#";
                    a.innerText = cat.name;
                    a.onclick = (e) => {
                        e.preventDefault();
                        switchCategory(cat.id, a);
                    };
                    nav.appendChild(a);
                });
            }
        } catch (e) { console.error("Load categories failed"); }
    }

    // 切换分类
    function switchCategory(catId, el) {
        currentCategoryId = catId;
        // 更新高亮
        document.querySelectorAll(".nav-links a").forEach(a => a.classList.remove("active"));
        el.classList.add("active");
        // 更新标题
        document.getElementById("section-title").innerText = catId ? el.innerText : "🔥 热门推荐";
        // 重新加载
        loadVideos(catId);
    }

    // 加载视频
    async function loadVideos(catId = null, keyword = null) {
        const grid = document.getElementById("video-grid");
        grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: #999; padding: 40px;">加载中...</div>';
        
        let url = `/videos?size=20&sort_by=latest`;
        if (catId) url += `&category_id=${catId}`;
        if (keyword) url += `&keyword=${encodeURIComponent(keyword)}`;

        try {
            const res = await fetch(url);
            if (res.ok) {
                const videos = await res.json();
                renderGrid(videos);
            } else {
                grid.innerHTML = '<p>加载失败</p>';
            }
        } catch (e) {
            grid.innerHTML = '<p>网络错误</p>';
        }
    }

    // 渲染网格
    function renderGrid(videos) {
        const grid = document.getElementById("video-grid");
        grid.innerHTML = "";
        
        if (videos.length === 0) {
            grid.innerHTML = '<div style="grid-column: 1/-1; text-align: center; color: #999; padding: 40px;">暂无相关内容 🍃</div>';
            return;
        }

        videos.forEach(v => {
            const card = document.createElement("div");
            card.className = "video-card";
            card.onclick = () => location.href = `/static/video.html?id=${v.id}`;
            
            // 格式化时间
            const date = new Date(v.created_at).toLocaleDateString();
            // 格式化时长 (秒 -> mm:ss)
            const min = Math.floor((v.duration || 0) / 60);
            const sec = (v.duration || 0) % 60;
            const timeStr = `${min}:${sec.toString().padStart(2, '0')}`;
            
            card.innerHTML = `
                <div class="thumbnail-box">
                    <img src="${v.thumbnail_path || '/static/default_cover.jpg'}" loading="lazy">
                    <div class="duration">${timeStr}</div>
                </div>
                <div class="info">
                    <div class="title" title="${v.title}">${v.title}</div>
                    <div class="meta">
                        <span class="author">UP ${v.owner ? v.owner.username : '匿名'}</span>
                        <span>📅 ${date}</span>
                        <span>👁️ ${v.views}</span>
                    </div>
                </div>
            `;
            grid.appendChild(card);
        });
    }

    function handleSearch(e) {
        if (e.key === 'Enter') {
            loadVideos(null, e.target.value);
        }
    }
</script>

</body>
</html>
"""

with open("/data/myvideo/static/index.html", "w", encoding="utf-8") as f:
    f.write(html_content)
print("Successfully generated index.html")

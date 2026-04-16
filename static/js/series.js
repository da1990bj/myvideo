var seriesId = null;
var currentVideoId = null;
var playlistVideos = [];

document.addEventListener("DOMContentLoaded", function() {
    var params = new URLSearchParams(window.location.search);
    seriesId = params.get("id");
    if (!seriesId) {
        alert("剧集不存在");
        window.location.href = "/";
        return;
    }
    loadSeries();

    document.getElementById("playlist-items").addEventListener("click", function(e) {
        var item = e.target.closest(".playlist-item");
        if (!item) return;
        if (item.classList.contains("unavailable")) return;
        var videoId = item.getAttribute("data-id");
        if (videoId) playVideo(videoId);
    });
});

function loadSeries() {
    fetch("/drama-series/" + seriesId)
        .then(function(res) { return res.json(); })
        .then(function(data) {
            document.getElementById("series-title").textContent = data.title || "剧集";
            document.getElementById("playlist-title").textContent = data.title || "选集";
            document.getElementById("playlist-count").textContent = (data.video_count || 0) + " 集";

            var metaHtml = "";
            if (data.drama_type) {
                var typeText = data.drama_type === "tv" ? "电视剧" : data.drama_type === "anime" ? "动漫" : "电影";
                metaHtml += "<span class='badge " + data.drama_type + "'>" + typeText + "</span> ";
            }
            if (data.drama_status) {
                var statusText = data.drama_status === "ongoing" ? "连载中" : "已完结";
                metaHtml += "<span class='badge " + data.drama_status + "'>" + statusText + "</span> ";
            }
            if (data.drama_region && data.drama_region.length) {
                metaHtml += data.drama_region.join(", ") + " ";
            }
            if (data.drama_year) {
                metaHtml += data.drama_year + "年 ";
            }
            document.getElementById("series-meta").innerHTML = metaHtml;

            var videos = data.videos || [];
            playlistVideos = videos;

            if (videos.length > 0) {
                playVideo(videos[0].id);
                renderPlaylist(videos);
            } else {
                document.getElementById("playlist-items").innerHTML = "<div class='no-video'>暂无视频</div>";
            }
        })
        .catch(function(e) {
            console.error(e);
            document.getElementById("playlist-items").innerHTML = "<div class='empty'>加载失败</div>";
        });
}

function playVideo(videoId) {
    var videoData = null;
    for (var i = 0; i < playlistVideos.length; i++) {
        if (playlistVideos[i].id === videoId) {
            videoData = playlistVideos[i];
            break;
        }
    }
    if (!videoData) return;
    currentVideoId = videoId;

    var playerWrapper = document.getElementById("player-wrapper");
    if (videoData.processed_file_path || videoData.original_file_path) {
        var videoSrc = videoData.processed_file_path || videoData.original_file_path;
        playerWrapper.innerHTML = "<video id='video-player' controls autoplay style='width:100%;height:100%;'><source src='" + videoSrc + "' type='video/mp4'></video>";
    } else {
        playerWrapper.innerHTML = "<div class='no-video'>视频不可用</div>";
    }

    document.getElementById("video-title").textContent = videoData.title || "无标题";

    var metaHtml = "";
    if (videoData.views) metaHtml += "<span>" + videoData.views + " 次播放</span> ";
    if (videoData.duration) metaHtml += "<span>" + formatDuration(videoData.duration) + "</span> ";
    document.getElementById("video-meta").innerHTML = metaHtml;

    var tagsHtml = "";
    if (videoData.tags && typeof videoData.tags === "string") {
        var tags = videoData.tags.split(",").map(function(t) { return t.trim(); }).filter(function(t) { return t; });
        tagsHtml = tags.map(function(t) { return "<span class='video-tag'>" + t + "</span>"; }).join("");
    }
    document.getElementById("video-tags").innerHTML = tagsHtml;

    var items = document.querySelectorAll(".playlist-item");
    for (var j = 0; j < items.length; j++) {
        items[j].classList.remove("active");
        if (items[j].getAttribute("data-id") === videoId) {
            items[j].classList.add("active");
        }
    }
}

function renderPlaylist(videos) {
    var container = document.getElementById("playlist-items");
    var html = "";
    for (var i = 0; i < videos.length; i++) {
        var v = videos[i];
        var thumb = v.thumbnail_path || "";
        var duration = v.duration ? formatDuration(v.duration) : "";
        var episodeTitle = v.title || ("第 " + (v.episode_number || (i + 1)) + " 集");
        var isPlayable = v.processed_file_path && v.status === "completed" && v.is_approved === "approved" && v.visibility === "public";

        var itemClass = "playlist-item" + (isPlayable ? "" : " unavailable");

        var thumbHtml = thumb ? "<img src='" + thumb + "' alt=''>" : "<div style='width:100%;height:100%;background:#f0f0f0;display:flex;align-items:center;justify-content:center;'>📺</div>";
        var durationHtml = duration ? "<span class='duration'>" + duration + "</span>" : "";
        var lockHtml = !isPlayable ? "<span class='duration' style='background:rgba(0,0,0,0.7);'>不可播放</span>" : "";
        var titleSuffix = !isPlayable ? " [不可播放]" : "";

        html += "<div class='" + itemClass + "' data-id='" + v.id + "'>" +
            "<div class='playlist-thumb'>" + thumbHtml + durationHtml + lockHtml + "</div>" +
            "<div class='playlist-info'>" +
            "<div class='playlist-title'>" + episodeTitle + titleSuffix + "</div>" +
            "<div class='playlist-meta'>" + (v.views || 0) + "次播放</div>" +
            "</div></div>";
    }
    container.innerHTML = html;
}

function formatDuration(seconds) {
    if (!seconds) return "";
    var h = Math.floor(seconds / 3600);
    var m = Math.floor((seconds % 3600) / 60);
    var s = Math.floor(seconds % 60);
    if (h > 0) {
        return h + ":" + (m < 10 ? "0" : "") + m + ":" + (s < 10 ? "0" : "") + s;
    }
    return m + ":" + (s < 10 ? "0" : "") + s;
}

function toggleLike() {
    var token = localStorage.getItem("access_token");
    if (!token) {
        alert("请先登录");
        window.location.href = "/static/login.html";
    }
}

function shareVideo() {
    var url = window.location.href;
    if (navigator.clipboard) {
        navigator.clipboard.writeText(url).then(function() { alert("链接已复制到剪贴板"); });
    } else {
        prompt("复制以下链接分享:", url);
    }
}

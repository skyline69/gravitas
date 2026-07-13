pragma Singleton
import QtQuick

// Poster-URL helper: return a smaller variant of a poster URL sized for the
// target display pixel width, so we don't download full-size art for small
// thumbnails. Falls back to the original URL for unknown hosts.
QtObject {
    function sized(url, w) {
        if (!url)
            return ""
        // Amazon (Cinemeta catalog art): ..._V1_SX250.jpg — swap the width
        // token, or inject one before the extension if absent.
        if (url.indexOf("media-amazon.com") !== -1 || url.indexOf("media-imdb.com") !== -1) {
            var swapped = url.replace(/_(S[XY]|U[XY])\d+/, "_SX" + w)
            if (swapped !== url)
                return swapped
            return url.replace(/\.(jpg|jpeg|png)$/i, "._V1_SX" + w + ".$1")
        }
        // Metahub: /poster/{small|medium|large}/... (images. and live. hosts)
        if (url.indexOf("metahub.space") !== -1) {
            var msize = w <= 120 ? "small" : (w <= 380 ? "medium" : "large")
            return url.replace(/\/poster\/(small|medium|large)\//, "/poster/" + msize + "/")
        }
        // TMDB: /t/p/w342/...
        if (url.indexOf("image.tmdb.org") !== -1) {
            var tsize = w <= 120 ? "w185" : (w <= 400 ? "w342" : "w500")
            return url.replace(/\/w\d+\//, "/" + tsize + "/")
        }
        return url
    }
}

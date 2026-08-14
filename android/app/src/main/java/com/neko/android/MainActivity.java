package com.neko.android;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.ContentValues;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.os.Handler;
import android.os.Looper;
import android.provider.MediaStore;
import android.provider.Settings;
import android.webkit.DownloadListener;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import com.chaquo.python.PyObject;
import com.chaquo.python.Python;

import java.io.BufferedInputStream;
import java.io.BufferedOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

public class MainActivity extends Activity {

    private static final String MAIN_SERVER_URL = "http://127.0.0.1:48911/?pet=1";
    private static final String HEALTH_URL = "http://127.0.0.1:48911/health";

    private WebView webView;
    private boolean serverStarted = false;
    private ValueCallback<Uri[]> filePathCallback;
    private static final int FILE_CHOOSER_REQUEST = 2001;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        WebView.setWebContentsDebuggingEnabled(true);

        webView = new WebView(this);

        // 开屏声明：本软件由网友制作的移植版，非官方版。
        // 用全屏遮罩叠加在 WebView 之上（WebView 照常加载，不阻塞后台
        // server 启动），点击按钮或 3 秒后自动淡出进入主界面。
        // 注意：setContentView 在 showSplashDeclaration 内统一设置，
        // 不能再先 setContentView(webView)，否则 addView 报已有 parent。
        showSplashDeclaration(webView);

        // 静态资源（/static/**）服务端已设 immutable 长缓存（web_app.py
        // CustomStaticFiles）。若每次启动都 clearCache，React bundle / Live2D
        // 等每次冷启动全部重新下载，是启动加载慢的主因。改为仅应用升级时清
        // 一次缓存：升级后 URL 的 ?v= 版本号会变，正好配合 immutable 拿到新资源。
        clearCacheOnUpgrade(webView);

        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setDatabaseEnabled(true);
        settings.setSupportMultipleWindows(true);
        settings.setJavaScriptCanOpenWindowsAutomatically(true);

        // 模拟器/设备系统处于深色模式时，WebView 默认 FORCE_DARK_AUTO 会把
        // 浅色页面自动压黑，且 theme-manager.js 会跟随 prefers-color-scheme
        // 切深色主题（整页黑底）。桌面端默认是浅色皮肤，这里强制 WebView 始终
        // 报告浅色配色（用户仍可用应用内深色开关显式切换）。
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            settings.setForceDark(WebSettings.FORCE_DARK_OFF);
        }
        // 页面渲染前/透明区域防止黑色透底闪屏。
        webView.setBackgroundColor(Color.WHITE);

        // JS 桥：前端"悬浮窗"入口调用。悬浮窗需要 SYSTEM_ALERT_WINDOW 权限，
        // 无权限时引导到系统设置授予。
        webView.addJavascriptInterface(new AndroidBridge(), "nekoAndroid");

        // 主界面加载完成即淡出开屏（与 8 秒保底 / 按钮互为兜底），
        // 用户永远看不到"开屏结束但页面没就绪"的空白。
        webView.setWebViewClient(new WebViewClient() {
            @Override
            public void onPageFinished(WebView view, String url) {
                super.onPageFinished(view, url);
                runOnUiThread(MainActivity.this::dismissSplash);
            }
        });
        // 记忆导出等 attachment 下载：WebView 触发 DownloadListener，保存到公共
        // 下载目录（API 29+ 走 MediaStore.Downloads，无需额外权限）。
        webView.setDownloadListener(new DownloadListener() {
            @Override
            public void onDownloadStart(String url, String userAgent, String contentDisposition,
                                        String mimetype, long contentLength) {
                downloadFile(url, contentDisposition, mimetype);
            }
        });
        // The desktop UI opens sub-pages (API key settings, character-card
        // manager, …) via window.open(). WebView blocks that by default, so
        // the user could never reach those pages. Route any popup window into
        // the single in-app WebView instead.
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onCreateWindow(WebView view, boolean isDialog, boolean isUserGesture,
                                          android.os.Message resultMsg) {
                final WebView popup = new WebView(view.getContext());
                popup.setWebViewClient(new WebViewClient() {
                    @Override
                    public boolean shouldOverrideUrlLoading(WebView v, String url) {
                        v.destroy();
                        webView.loadUrl(url);
                        return true;
                    }
                });
                WebView.WebViewTransport transport = (WebView.WebViewTransport) resultMsg.obj;
                transport.setWebView(popup);
                resultMsg.sendToTarget();
                return true;
            }

            @Override
            public boolean onShowFileChooser(WebView webView, ValueCallback<Uri[]> filePathCallback,
                                             FileChooserParams fileChooserParams) {
                if (MainActivity.this.filePathCallback != null) {
                    MainActivity.this.filePathCallback.onReceiveValue(null);
                }
                MainActivity.this.filePathCallback = filePathCallback;
                Intent intent = fileChooserParams.createIntent();
                intent.addCategory(Intent.CATEGORY_OPENABLE);
                try {
                    startActivityForResult(intent, FILE_CHOOSER_REQUEST);
                } catch (Exception e) {
                    filePathCallback.onReceiveValue(null);
                    MainActivity.this.filePathCallback = null;
                }
                return true;
            }
        });

        startServer();
        pollHealth();
        FloatingWindowService.attachActivity(this);

        // Shizuku 授权结果回调：授权完成后前端可感知（status 轮询）。
        try {
            rikka.shizuku.Shizuku.addRequestPermissionResultListener(
                    (requestCode, grantResult) -> {
                        android.util.Log.d("NekoShizuku", "permission result code="
                                + requestCode + " granted="
                                + (grantResult == android.content.pm.PackageManager.PERMISSION_GRANTED));
                        if (grantResult == android.content.pm.PackageManager.PERMISSION_GRANTED) {
                            // 授权后预热 UserService 绑定，前端执行命令即用即得。
                            new Handler(Looper.getMainLooper()).postDelayed(
                                    NekoShizuku::ensureShellBound, 500);
                        }
                    });
        } catch (Exception e) {
            // Shizuku 未安装 / 服务不可用时监听注册失败不影响应用。
        }
        // 已授权过（应用重启）也预热一次。
        if (NekoShizuku.isReady()) {
            NekoShizuku.ensureShellBound();
        }
    }

    private void showSplashDeclaration(WebView webView) {
        final android.widget.FrameLayout root = new android.widget.FrameLayout(this);
        root.addView(webView, new android.widget.FrameLayout.LayoutParams(
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT));
        setContentView(root);

        final android.widget.LinearLayout splash = new android.widget.LinearLayout(this);
        splash.setOrientation(android.widget.LinearLayout.VERTICAL);
        splash.setGravity(android.view.Gravity.CENTER);
        splash.setBackgroundColor(Color.WHITE);
        splash.setLayoutParams(new android.widget.FrameLayout.LayoutParams(
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT,
                android.widget.FrameLayout.LayoutParams.MATCH_PARENT));

        android.widget.TextView title = new android.widget.TextView(this);
        title.setText("N.E.K.O");
        title.setTextSize(32);
        title.setTextColor(Color.parseColor("#4AA8E8"));
        title.setGravity(android.view.Gravity.CENTER);
        splash.addView(title);

        android.widget.TextView subtitle = new android.widget.TextView(this);
        subtitle.setText("猫娘 · AI 助手");
        subtitle.setTextSize(14);
        subtitle.setTextColor(Color.parseColor("#8AA6B8"));
        subtitle.setGravity(android.view.Gravity.CENTER);
        subtitle.setPadding(0, 4, 0, 40);
        splash.addView(subtitle);

        android.widget.TextView notice = new android.widget.TextView(this);
        notice.setText("本软件是由网友制作的移植版，非官方版");
        notice.setTextSize(16);
        notice.setTextColor(Color.parseColor("#335566"));
        notice.setGravity(android.view.Gravity.CENTER);
        notice.setPadding(48, 16, 48, 16);
        splash.addView(notice);

        android.widget.TextView featuresHeader = new android.widget.TextView(this);
        featuresHeader.setText("以下重要功能仅电脑版可用，移动版未移植 / 未完善：");
        featuresHeader.setTextSize(13);
        featuresHeader.setTextColor(Color.parseColor("#4AA8E8"));
        featuresHeader.setGravity(android.view.Gravity.CENTER);
        featuresHeader.setPadding(24, 8, 24, 2);
        splash.addView(featuresHeader);

        android.widget.TextView features = new android.widget.TextView(this);
        features.setText(
                "· 桌面猫娘形象（Live2D / VRM / MMD 3D 模型、PNG 立绘）\n"
                + "· 屏幕感知搭话（电脑截屏、窗口标题识别）\n"
                + "· 鼠标键盘自动化与电脑操作\n"
                + "· 浏览器自动化 / 电脑控制（Computer Use）\n"
                + "· 游戏联动（GalGame、桌面小游戏）\n"
                + "· Steam 云存档 / 成就 / 创意工坊\n"
                + "· VMC 全身动捕协议\n"
                + "· 系统托盘 / 全局快捷键 / 桌面音乐播放联动");
        features.setTextSize(12);
        features.setTextColor(Color.parseColor("#55758C"));
        features.setGravity(android.view.Gravity.CENTER);
        features.setPadding(40, 4, 40, 4);
        features.setLineSpacing(0f, 1.2f);
        splash.addView(features);

        android.widget.TextView tip = new android.widget.TextView(this);
        tip.setText("请勿将本软件用于任何商业用途");
        tip.setTextSize(12);
        tip.setTextColor(Color.parseColor("#B9CFE0"));
        tip.setGravity(android.view.Gravity.CENTER);
        tip.setPadding(0, 6, 0, 20);
        splash.addView(tip);

        android.widget.Button enter = new android.widget.Button(this);
        enter.setText("进入应用");
        splash.addView(enter);

        root.addView(splash);
        root.bringChildToFront(splash);
        android.util.Log.d("NekoSplash", "splash shown");

        final Runnable dismiss = () -> {
            if (splash.getParent() != null) {
                root.removeView(splash);
                android.util.Log.d("NekoSplash", "splash dismissed");
            }
        };
        splashDismiss = dismiss;
        enter.setOnClickListener(v -> dismissSplash());
        // 8 秒后自动进入（功能清单需要阅读时间）；主界面加载完成也会淡出
        // （WebViewClient.onPageFinished）。三者互为兜底。
        new Handler(Looper.getMainLooper()).postDelayed(this::dismissSplash, 8000);
    }

    // 开屏淡出（幂等）：按钮 / 8 秒保底 / 主界面加载完成都会调用。
    private void dismissSplash() {
        Runnable r;
        synchronized (this) {
            r = splashDismiss;
            splashDismiss = null;
        }
        if (r != null) {
            runOnUiThread(r);
        }
    }

    private void clearCacheOnUpgrade(WebView webView) {
        try {
            int ver = getPackageManager().getPackageInfo(getPackageName(), 0).versionCode;
            android.content.SharedPreferences prefs =
                    getSharedPreferences("neko_cache", MODE_PRIVATE);
            if (prefs.getInt("last_version_code", 0) != ver) {
                webView.clearCache(true);
                prefs.edit().putInt("last_version_code", ver).apply();
                android.util.Log.d("NekoCache", "cache cleared on upgrade to v" + ver);
            }
        } catch (Exception e) {
            // 清缓存失败不影响功能，资源按需重新下载即可。
        }
    }

    private void startServer() {
        if (serverStarted) {
            return;
        }
        serverStarted = true;        Thread serverThread = new Thread(() -> {
            try {
                Python py = Python.getInstance();
                PyObject entry = py.getModule("android_entry");
                entry.callAttr("main");
            } catch (Throwable t) {
                // Server loop is long-running; errors go to logcat.
                t.printStackTrace();
            }
        }, "neko-server");
        serverThread.setDaemon(true);
        serverThread.start();
    }

    private void downloadFile(String url, String contentDisposition, String mimetype) {
        String filename = "neko-download.tar.gz";
        if (contentDisposition != null) {
            for (String part : contentDisposition.split(";")) {
                String trimmed = part.trim();
                if (trimmed.startsWith("filename=")) {
                    String candidate = trimmed.substring("filename=".length()).replace("\"", "").trim();
                    if (!candidate.isEmpty()) {
                        filename = candidate;
                        break;
                    }
                }
            }
        }
        if (mimetype == null || mimetype.isEmpty()) {
            mimetype = "application/octet-stream";
        }
        final String targetFilename = filename;
        final String targetMimetype = mimetype;
        new Thread(() -> {
            InputStream in = null;
            OutputStream out = null;
            boolean saved = false;
            try {
                HttpURLConnection conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setConnectTimeout(15000);
                conn.setReadTimeout(30000);
                int code = conn.getResponseCode();
                if (code != 200) {
                    return;
                }
                in = new BufferedInputStream(conn.getInputStream());
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                    ContentValues values = new ContentValues();
                    values.put(MediaStore.Downloads.DISPLAY_NAME, targetFilename);
                    values.put(MediaStore.Downloads.MIME_TYPE, targetMimetype);
                    values.put(MediaStore.Downloads.IS_PENDING, 1);
                    Uri item = getContentResolver().insert(
                            MediaStore.Downloads.getContentUri(MediaStore.VOLUME_EXTERNAL_PRIMARY),
                            values);
                    if (item == null) {
                        return;
                    }
                    out = new BufferedOutputStream(getContentResolver().openOutputStream(item));
                    byte[] buffer = new byte[8192];
                    int read;
                    while ((read = in.read(buffer)) != -1) {
                        out.write(buffer, 0, read);
                    }
                    out.flush();
                    out.close();
                    out = null;
                    values.clear();
                    values.put(MediaStore.Downloads.IS_PENDING, 0);
                    getContentResolver().update(item, values, null, null);
                    saved = true;
                } else {
                    File dir = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS);
                    File target = new File(dir, targetFilename);
                    out = new BufferedOutputStream(new FileOutputStream(target));
                    byte[] buffer = new byte[8192];
                    int read;
                    while ((read = in.read(buffer)) != -1) {
                        out.write(buffer, 0, read);
                    }
                    saved = true;
                }
            } catch (Exception e) {
                e.printStackTrace();
            } finally {
                try {
                    if (out != null) {
                        out.close();
                    }
                    if (in != null) {
                        in.close();
                    }
                } catch (IOException ignored) {
                }
                if (saved) {
                    final String toast = targetFilename;
                    new Handler(Looper.getMainLooper()).post(() ->
                            android.widget.Toast.makeText(MainActivity.this,
                                    "已下载：" + toast, android.widget.Toast.LENGTH_LONG).show());
                }
            }
        }, "neko-download").start();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == FILE_CHOOSER_REQUEST) {
            if (filePathCallback == null) {
                return;
            }
            Uri[] results = null;
            if (resultCode == Activity.RESULT_OK && data != null && data.getData() != null) {
                results = new Uri[]{data.getData()};
            }
            filePathCallback.onReceiveValue(results);
            filePathCallback = null;
        } else {
            super.onActivityResult(requestCode, resultCode, data);
        }
    }

    private void pollHealth() {
        final Handler handler = new Handler(Looper.getMainLooper());
        new Thread(() -> {
            long deadline = System.currentTimeMillis() + 90000;
            while (System.currentTimeMillis() < deadline) {
                if (isServerReady()) {
                    handler.post(() -> webView.loadUrl(MAIN_SERVER_URL));
                    return;
                }
                try {
                    Thread.sleep(1000);
                } catch (InterruptedException e) {
                    return;
                }
            }
            handler.post(() -> webView.loadUrl(MAIN_SERVER_URL));
        }, "neko-health-poll").start();
    }

    private boolean isServerReady() {
        try {
            HttpURLConnection conn = (HttpURLConnection) new URL(HEALTH_URL).openConnection();
            conn.setConnectTimeout(1500);
            conn.setReadTimeout(1500);
            int code = conn.getResponseCode();
            conn.disconnect();
            return code == 200;
        } catch (IOException e) {
            return false;
        }
    }
    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }

    // ---- 猫娘悬浮窗 ----

    private boolean pendingFloatingRequest = false;
    private boolean mainWsBlocked = false;
    private Runnable splashDismiss;

    private static final String MAIN_WS_BLOCK_JS =
            "(function () {" +
            "  var w = window;" +
            "  if (w.__nekoMainWsBlocked) return;" +
            "  w.__nekoMainWsBlocked = true;" +
            "  w.WebSocket = function (url, protocols) {" +
            "    var noop = function () {};" +
            "    return { url: String(url), readyState: 3, bufferedAmount: 0, extensions: '', protocol: ''," +
            "      onopen: null, onmessage: null, onclose: null, onerror: null," +
            "      binaryType: 'blob', send: noop, close: noop, addEventListener: noop," +
            "      removeEventListener: noop, dispatchEvent: function () { return false; } };" +
            "  };" +
            "  try { w.WebSocket.CONNECTING = 0; w.WebSocket.OPEN = 1; w.WebSocket.CLOSING = 2; w.WebSocket.CLOSED = 3; } catch (e) {}" +
            "})();";

    private void injectMainWsBlock() {
        if (mainWsBlocked || webView == null) {
            return;
        }
        mainWsBlocked = true;
        webView.evaluateJavascript(MAIN_WS_BLOCK_JS, null);
    }

    private void restoreMainWs() {
        if (!mainWsBlocked || webView == null) {
            return;
        }
        mainWsBlocked = false;
        webView.reload();
    }

    public void onFloatingStopped() {
        runOnUiThread(this::restoreMainWs);
    }

    private final class AndroidBridge {
        @android.webkit.JavascriptInterface
        public void openFloatingWindow() {
            runOnUiThread(() -> {
                if (Settings.canDrawOverlays(MainActivity.this)) {
                    // 悬浮窗成为"最新会话"前，先让主界面放弃 WebSocket 抢占：
                    // 主界面被踢后的自动重连会拿到 fake 连接，不再与悬浮窗互抢。
                    injectMainWsBlock();
                    ensureReadMediaImagesPermission();
                    ensureCameraPermission();
                    startFloatingService();
                } else {
                    pendingFloatingRequest = true;
                    requestOverlayPermission();
                }
            });
        }

        @android.webkit.JavascriptInterface
        public void isFloatingActive() {
            // 无操作占位：保持桥接口稳定。
        }

        // ---- Shizuku（adb 能力）----

        @android.webkit.JavascriptInterface
        public String shizukuStatus() {
            return "{\"service\":" + NekoShizuku.isServiceAvailable()
                    + ",\"granted\":" + NekoShizuku.isPermissionGranted()
                    + ",\"ready\":" + NekoShizuku.isReady() + "}";
        }

        @android.webkit.JavascriptInterface
        public void requestShizuku() {
            runOnUiThread(NekoShizuku::requestPermission);
        }

        /** 执行白名单内的 shell 命令，返回 JSON：{ok, code, stdout, stderr}。 */
        @android.webkit.JavascriptInterface
        public String execShell(String command) {
            return NekoShizuku.execShellCommand(command);
        }
    }

    private void startFloatingService() {
        Intent serviceIntent = new Intent(this, FloatingWindowService.class);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            startForegroundService(serviceIntent);
        } else {
            startService(serviceIntent);
        }
    }

    private static final int REQ_READ_MEDIA_IMAGES = 2003;

    // 截屏主动搭话需要 READ_MEDIA_IMAGES 才能读 MediaStore 图片元数据；
    // 开启悬浮窗时顺带请求一次（不阻塞启动，拒绝则截屏检测降级为不触发）。
    private void ensureReadMediaImagesPermission() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(android.Manifest.permission.READ_MEDIA_IMAGES)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{android.Manifest.permission.READ_MEDIA_IMAGES},
                    REQ_READ_MEDIA_IMAGES);
        }
    }

    private static final int REQ_CAMERA = 2004;

    // 猫娘"看到世界"需要 CAMERA；开启悬浮窗时顺带请求（拒绝则摄像头搭话不工作，
    // 悬浮窗其余功能不受影响）。
    private void ensureCameraPermission() {
        if (checkSelfPermission(android.Manifest.permission.CAMERA)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(
                    new String[]{android.Manifest.permission.CAMERA},
                    REQ_CAMERA);
        }
    }

    private void requestOverlayPermission() {
        Intent intent = new Intent(
                Settings.ACTION_MANAGE_OVERLAY_PERMISSION,
                Uri.parse("package:" + getPackageName()));
        try {
            startActivity(intent);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (pendingFloatingRequest && Settings.canDrawOverlays(this)) {
            pendingFloatingRequest = false;
            injectMainWsBlock();
            ensureReadMediaImagesPermission();
            ensureCameraPermission();
            startFloatingService();
        }
        // 悬浮窗仍激活时，确保主界面处于 ws 旁观状态，避免重连抢占会话。
        if (FloatingWindowService.isActive()) {
            injectMainWsBlock();
        }
    }

    @Override
    protected void onDestroy() {
        FloatingWindowService.detachActivity();
        super.onDestroy();
    }
}

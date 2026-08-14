package com.neko.android;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.database.ContentObserver;
import android.database.Cursor;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.PixelFormat;
import android.graphics.Point;
import android.graphics.Rect;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.media.Image;
import android.media.ImageReader;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.os.Looper;
import android.provider.MediaStore;
import android.util.Base64;
import android.view.Display;
import android.view.Gravity;
import android.view.LayoutInflater;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/**
 * Foreground service hosting the mini "pet widget" floating window on top of
 * other apps.
 *
 * Focus policy: the window is NOT_FOCUSABLE by default so it never steals
 * touch/focus from the app underneath. Tapping the WebView (to type) removes
 * NOT_FOCUSABLE temporarily; when the keyboard closes, NOT_FOCUSABLE is
 * restored so the underlying app is usable again.
 *
 * The window can be minimized to a small badge (tap to restore) and dragged
 * by the title bar / badge.
 */
public class FloatingWindowService extends Service {

    public static final String ACTION_STOP = "com.neko.android.action.FLOATING_STOP";
    public static final String MAIN_SERVER_URL = "http://127.0.0.1:48911/widget_input";

    private static final String CHANNEL_ID = "neko_floating";
    private static final int NOTIFICATION_ID = 2002;
    private static final float WINDOW_WIDTH_RATIO = 0.48f;
    private static final float WINDOW_HEIGHT_RATIO = 0.8f;
    private static final int MINI_SIZE_DP = 56;
    // 摄像头定时自动感知间隔（毫秒）：默认 90 秒抓一帧。
    private static final long CAMERA_AUTO_INTERVAL_MS = 90_000L;
    private static final int CAMERA_FRAME_WIDTH = 640;
    private static final int CAMERA_FRAME_HEIGHT = 480;

    private static boolean sActive = false;
    private static MainActivity sMainActivityRef;

    public static boolean isActive() {
        return sActive;
    }

    public static void attachActivity(MainActivity activity) {
        sMainActivityRef = activity;
    }

    public static void detachActivity() {
        sMainActivityRef = null;
    }

    private WindowManager windowManager;
    private View floatingView;
    private WindowManager.LayoutParams layoutParams;
    private WebView webView;
    private boolean added = false;
    private boolean minimized = false;
    private boolean inputFocusActive = false;
    private int fullWidth;
    private int fullHeight;
    private ContentObserver screenshotObserver;
    private long lastScreenshotNotifyAt = 0;
    private final Handler cameraHandler = new Handler(Looper.getMainLooper());
    private Runnable cameraAutoGlanceRunnable;

    @Override
    public void onCreate() {
        super.onCreate();
        createNotificationChannel();
        windowManager = (WindowManager) getSystemService(WINDOW_SERVICE);
        startScreenshotObserver();
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null && ACTION_STOP.equals(intent.getAction())) {
            stopFloating();
            return START_NOT_STICKY;
        }
        sActive = true;
        startForeground(NOTIFICATION_ID, buildNotification());
        if (!added) {
            setupFloatingView();
        }
        return START_STICKY;
    }

    private void createNotificationChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) {
            return;
        }
        NotificationChannel channel = new NotificationChannel(
                CHANNEL_ID,
                "猫娘悬浮窗",
                NotificationManager.IMPORTANCE_LOW);
        NotificationManager nm = getSystemService(NotificationManager.class);
        if (nm != null) {
            nm.createNotificationChannel(channel);
        }
    }

    private Notification buildNotification() {
        Intent openIntent = new Intent(this, MainActivity.class);
        openIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        PendingIntent pi = PendingIntent.getActivity(this, 0, openIntent, PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            builder = new Notification.Builder(this, CHANNEL_ID);
        } else {
            builder = new Notification.Builder(this);
        }
        return builder
                .setContentTitle("N.E.K.O 猫娘挂件")
                .setContentText("点击返回主界面")
                .setSmallIcon(android.R.drawable.ic_menu_myplaces)
                .setContentIntent(pi)
                .setOngoing(true)
                .build();
    }

    private void setupFloatingView() {
        floatingView = LayoutInflater.from(this).inflate(R.layout.floating_pet_window, null);
        webView = floatingView.findViewById(R.id.floating_webview);
        configureWebView(webView);

        floatingView.findViewById(R.id.floating_open).setOnClickListener(v -> {
            Intent i = new Intent(this, MainActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startActivity(i);
        });
        floatingView.findViewById(R.id.floating_close).setOnClickListener(v -> stopFloating());
        floatingView.findViewById(R.id.floating_minimize).setOnClickListener(v -> minimize());
        View cameraBtn = floatingView.findViewById(R.id.floating_camera);
        if (cameraBtn != null) {
            cameraBtn.setOnClickListener(v -> captureFrameAndUpload("manual"));
        }
        View miniView = floatingView.findViewById(R.id.mini_view);
        miniView.setOnClickListener(v -> restore());

        floatingView.findViewById(R.id.floating_drag).setOnTouchListener(new DragTouchListener());
        floatingView.findViewById(R.id.floating_mini_drag).setOnTouchListener(new DragTouchListener());

        Display display = windowManager.getDefaultDisplay();
        Point size = new Point();
        display.getSize(size);
        fullWidth = (int) (size.x * WINDOW_WIDTH_RATIO);
        fullHeight = (int) (fullWidth * WINDOW_HEIGHT_RATIO);

        int type = Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                ? WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY
                : WindowManager.LayoutParams.TYPE_PHONE;
        // 默认 NOT_FOCUSABLE：悬浮窗悬浮在其他应用上层时不抢焦点/触摸，
        // 下层应用可正常操作。点击输入框才临时可聚焦（见 configureWebView）。
        layoutParams = new WindowManager.LayoutParams(
                fullWidth,
                fullHeight,
                type,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE
                        | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                PixelFormat.TRANSLUCENT);
        layoutParams.gravity = Gravity.TOP | Gravity.END;
        layoutParams.x = 0;
        layoutParams.y = 100;
        layoutParams.softInputMode = WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE;

        try {
            windowManager.addView(floatingView, layoutParams);
            added = true;
        } catch (Exception e) {
            e.printStackTrace();
            stopSelf();
            return;
        }

        // 恢复 NOT_FOCUSABLE 的时机：窗口失焦（用户点击下层应用）。
        // 延迟 250ms 再恢复，避免点击窗口内元素时误触发。
        floatingView.getViewTreeObserver().addOnWindowFocusChangeListener(hasFocus -> {
            if (!hasFocus && inputFocusActive) {
                new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
                    if (inputFocusActive) {
                        inputFocusActive = false;
                        applyNotFocusable();
                    }
                }, 250);
            }
        });

        // 延迟加载：等悬浮窗窗口完成布局/可见后再初始化页面
        new android.os.Handler(android.os.Looper.getMainLooper()).postDelayed(() -> {
            if (webView != null) {
                webView.loadUrl(MAIN_SERVER_URL);
            }
        }, 800);

        // 摄像头定时自动感知（猫娘定时看一眼世界）。minimized / 无权限时
        // captureFrameAndUpload 内部自然跳过。
        startCameraAutoGlance();
    }

    private void configureWebView(WebView wv) {
        WebSettings settings = wv.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setDatabaseEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            settings.setForceDark(WebSettings.FORCE_DARK_OFF);
        }
        wv.setBackgroundColor(Color.WHITE);
        wv.setWebViewClient(new WebViewClient());
        // JS 桥：widget 前端（聊天命令/按钮）可主动让猫娘"看一帧"。
        wv.addJavascriptInterface(new NekoCamBridge(), "nekoCam");
        // JS 桥：Shizuku adb 能力（白名单 shell 命令、免权限截屏、模拟输入）。
        wv.addJavascriptInterface(new ShizukuBridge(), "nekoShizuku");
        // 点击 WebView（输入框）时临时移除 NOT_FOCUSABLE：弹出键盘可输入；
        // 键盘收起后由 GlobalLayoutListener 恢复。
        wv.setOnTouchListener((v, event) -> {
            if (event.getAction() == MotionEvent.ACTION_UP) {
                makeFocusable();
            }
            return false;
        });
    }

    // ── 猫娘摄像头（看到世界）─────────────────────────────────────────
    // 手动：悬浮窗"看"按钮 / widget 前端 nekoCam.captureCamera()；
    // 定时：cameraAutoGlanceRunnable 每 90s 抓一帧（trigger=auto）。
    // 抓到的帧传给 widget 页面 window.__nekoOnCameraFrame(b64, trigger)，
    // 由页面携带 CSRF 上报主程序 /api/vision/camera-frame。

    private final class NekoCamBridge {
        @android.webkit.JavascriptInterface
        public void captureCamera() {
            new Handler(Looper.getMainLooper()).post(() -> captureFrameAndUpload("manual"));
        }
    }

    // Shizuku adb 能力（白名单 shell 命令），悬浮窗 widget 前端可调用。
    private final class ShizukuBridge {
        @android.webkit.JavascriptInterface
        public String shizukuStatus() {
            return "{\"service\":" + NekoShizuku.isServiceAvailable()
                    + ",\"granted\":" + NekoShizuku.isPermissionGranted()
                    + ",\"ready\":" + NekoShizuku.isReady() + "}";
        }

        @android.webkit.JavascriptInterface
        public void requestShizuku() {
            new Handler(Looper.getMainLooper()).post(NekoShizuku::requestPermission);
        }

        @android.webkit.JavascriptInterface
        public String execShell(String command) {
            return NekoShizuku.execShellCommand(command);
        }
    }

    private void captureFrameAndUpload(String trigger) {
        if (webView == null || minimized) {
            return;
        }
        captureCameraFrame(jpeg -> {
            if (jpeg == null || jpeg.length == 0) {
                return;
            }
            String b64 = Base64.encodeToString(jpeg, Base64.NO_WRAP);
            new Handler(Looper.getMainLooper()).post(() -> {
                if (webView != null) {
                    String escaped = b64.replace("\\", "\\\\").replace("'", "\\'");
                    webView.evaluateJavascript(
                            "window.__nekoOnCameraFrame && window.__nekoOnCameraFrame('" + escaped + "', '" + trigger + "')",
                            null);
                }
            });
        });
    }

    private interface FrameCallback {
        void onFrame(byte[] jpeg);
    }

    private void captureCameraFrame(FrameCallback cb) {
        if (Build.VERSION.SDK_INT < 21
                || checkSelfPermission(android.Manifest.permission.CAMERA)
                != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            android.util.Log.w("NekoCam", "captureFrame: no camera permission");
            cb.onFrame(null);
            return;
        }
        CameraManager cm = (CameraManager) getSystemService(Context.CAMERA_SERVICE);
        String cameraId = null;
        String fallbackId = null;
        try {
            for (String id : cm.getCameraIdList()) {
                CameraCharacteristics ch = cm.getCameraCharacteristics(id);
                Integer facing = ch.get(CameraCharacteristics.LENS_FACING);
                android.util.Log.d("NekoCam", "camera id=" + id + " facing=" + facing);
                if (fallbackId == null) {
                    fallbackId = id;
                }
                if (facing != null && facing == CameraCharacteristics.LENS_FACING_BACK) {
                    cameraId = id;
                    break;
                }
            }
        } catch (Exception e) {
            android.util.Log.w("NekoCam", "captureFrame: list cameras failed", e);
            cb.onFrame(null);
            return;
        }
        if (cameraId == null) {
            // 无后置摄像头（模拟器/特殊设备）：退回第一个可用摄像头。
            cameraId = fallbackId;
            android.util.Log.d("NekoCam", "captureFrame: no back camera, using fallback=" + cameraId);
        }
        if (cameraId == null) {
            android.util.Log.w("NekoCam", "captureFrame: no camera found");
            cb.onFrame(null);
            return;
        }
        // 从设备能力里挑一个支持的 JPEG 输出尺寸（≤1280x720，模拟器虚拟
        // 摄像头支持列表有限，硬编码 640x480 可能不被支持导致 capture 失败）。
        android.util.Size frameSize = null;
        try {
            CameraCharacteristics ch = cm.getCameraCharacteristics(cameraId);
            android.util.Size[] jpegSizes = ch.get(
                    CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP)
                    .getOutputSizes(ImageFormat.JPEG);
            if (jpegSizes != null) {
                for (android.util.Size s : jpegSizes) {
                    if (s.getWidth() <= 1280 && s.getHeight() <= 720) {
                        frameSize = s;
                        break;
                    }
                }
                if (frameSize == null && jpegSizes.length > 0) {
                    frameSize = jpegSizes[jpegSizes.length - 1];
                }
            }
        } catch (Exception e) {
            android.util.Log.w("NekoCam", "captureFrame: query jpeg sizes failed", e);
        }
        if (frameSize == null) {
            frameSize = new android.util.Size(CAMERA_FRAME_WIDTH, CAMERA_FRAME_HEIGHT);
        }
        final android.util.Size chosenSize = frameSize;
        android.util.Log.d("NekoCam", "captureFrame: camera=" + cameraId + " size=" + chosenSize);
        final HandlerThread ht = new HandlerThread("neko-cam");
        ht.start();
        final Handler h = new Handler(ht.getLooper());
        final ImageReader reader = ImageReader.newInstance(
                chosenSize.getWidth(), chosenSize.getHeight(), PixelFormat.JPEG, 2);
        final CameraDevice[] deviceRef = new CameraDevice[1];

        final Runnable cleanupRun = () -> {
            if (deviceRef[0] != null) {
                try {
                    deviceRef[0].close();
                } catch (Exception ignored) {
                }
            }
            try {
                reader.close();
            } catch (Exception ignored) {
            }
            ht.quitSafely();
        };

        reader.setOnImageAvailableListener(r -> {
            Image img = null;
            byte[] jpeg = null;
            try {
                img = r.acquireLatestImage();
                if (img != null) {
                    java.nio.ByteBuffer buf = img.getPlanes()[0].getBuffer();
                    jpeg = new byte[buf.remaining()];
                    buf.get(jpeg);
                }
            } catch (Exception e) {
                android.util.Log.w("NekoCam", "captureFrame: acquire image failed", e);
                jpeg = null;
            } finally {
                if (img != null) {
                    img.close();
                }
                cleanupRun.run();
                android.util.Log.d("NekoCam", "captureFrame: jpegLen=" + (jpeg != null ? jpeg.length : -1));
                if (jpeg != null) {
                    cb.onFrame(jpeg);
                } else {
                    cb.onFrame(null);
                }
            }
        }, h);

        try {
            cm.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    deviceRef[0] = camera;
                    android.util.Log.d("NekoCam", "captureFrame: camera opened");
                    try {
                        CaptureRequest.Builder req = camera.createCaptureRequest(
                                CameraDevice.TEMPLATE_STILL_CAPTURE);
                        req.addTarget(reader.getSurface());
                        req.set(CaptureRequest.JPEG_QUALITY, (byte) 80);
                        camera.createCaptureSession(
                                java.util.Collections.singletonList(reader.getSurface()),
                                new CameraCaptureSession.StateCallback() {
                                    @Override
                                    public void onConfigured(CameraCaptureSession session) {
                                        android.util.Log.d("NekoCam", "captureFrame: session configured");
                                        try {
                                            session.capture(req.build(), null, h);
                                        } catch (Exception e) {
                                            android.util.Log.w("NekoCam", "captureFrame: capture failed", e);
                                            cleanupRun.run();
                                            cb.onFrame(null);
                                        }
                                    }

                                    @Override
                                    public void onConfigureFailed(CameraCaptureSession session) {
                                        android.util.Log.w("NekoCam", "captureFrame: session configure failed");
                                        cleanupRun.run();
                                        cb.onFrame(null);
                                    }
                                }, h);
                    } catch (Exception e) {
                        android.util.Log.w("NekoCam", "captureFrame: build request failed", e);
                        cleanupRun.run();
                        cb.onFrame(null);
                    }
                }

                @Override
                public void onDisconnected(CameraDevice camera) {
                    android.util.Log.w("NekoCam", "captureFrame: camera disconnected");
                    cleanupRun.run();
                    cb.onFrame(null);
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    android.util.Log.w("NekoCam", "captureFrame: camera error " + error);
                    cleanupRun.run();
                    cb.onFrame(null);
                }
            }, h);
        } catch (Exception e) {
            android.util.Log.w("NekoCam", "captureFrame: openCamera failed", e);
            cleanupRun.run();
            cb.onFrame(null);
        }
    }

    private void startCameraAutoGlance() {
        if (cameraAutoGlanceRunnable != null) {
            return;
        }
        cameraAutoGlanceRunnable = new Runnable() {
            @Override
            public void run() {
                if (!minimized && webView != null) {
                    captureFrameAndUpload("auto");
                }
                cameraHandler.postDelayed(this, CAMERA_AUTO_INTERVAL_MS);
            }
        };
        cameraHandler.postDelayed(cameraAutoGlanceRunnable, CAMERA_AUTO_INTERVAL_MS);
    }

    private void stopCameraAutoGlance() {
        if (cameraAutoGlanceRunnable != null) {
            cameraHandler.removeCallbacks(cameraAutoGlanceRunnable);
            cameraAutoGlanceRunnable = null;
        }
    }

    // ── 截屏检测：用户截图时通知前端，让 YUI 主动搭话 ──
    // 通过 MediaStore ContentObserver 监听图片插入（API 29+ 无需存储权限），
    // 命中 Screenshots 目录的新文件即视为一次截图。onChange 对同一次截图可能
    // 触发多次，用 2.5s 节流合并。
    private void startScreenshotObserver() {
        if (screenshotObserver != null || Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
            return;
        }
        screenshotObserver = new ContentObserver(new Handler(Looper.getMainLooper())) {
            @Override
            public void onChange(boolean selfChange, Uri uri) {
                handlePossibleScreenshot();
            }
        };
        try {
            getContentResolver().registerContentObserver(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    true,
                    screenshotObserver);
        } catch (Exception e) {
            e.printStackTrace();
            screenshotObserver = null;
        }
    }

    private void handlePossibleScreenshot() {
        long now = System.currentTimeMillis();
        if (now - lastScreenshotNotifyAt < 2500) {
            return;
        }
        String[] projection = {
                MediaStore.Images.Media.DISPLAY_NAME,
                MediaStore.Images.Media.RELATIVE_PATH,
        };
        Cursor c = null;
        try {
            c = getContentResolver().query(
                    MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                    projection,
                    null,
                    null,
                    MediaStore.Images.Media.DATE_ADDED + " DESC");
            if (c != null && c.moveToFirst()) {
                String name = c.getString(0);
                String path = c.getString(1);
                boolean isScreenshot =
                        (name != null && name.toLowerCase().contains("screenshot"))
                        || (path != null && path.toLowerCase().contains("screenshot"));
                if (isScreenshot) {
                    lastScreenshotNotifyAt = now;
                    notifyScreenshotToWeb();
                }
            }
        } catch (Exception e) {
            // 截图检测是锦上添花，查询失败绝不打扰悬浮窗主功能。
        } finally {
            if (c != null) {
                c.close();
            }
        }
    }

    private void notifyScreenshotToWeb() {
        if (webView == null || minimized) {
            return;
        }
        new Handler(Looper.getMainLooper()).post(() -> {
            if (webView != null) {
                webView.evaluateJavascript(
                        "window.__nekoOnScreenshot && window.__nekoOnScreenshot()",
                        null);
            }
        });
    }

    private void stopScreenshotObserver() {
        if (screenshotObserver != null) {
            try {
                getContentResolver().unregisterContentObserver(screenshotObserver);
            } catch (Exception ignored) {
            }
            screenshotObserver = null;
        }
    }

    private void makeFocusable() {
        if (minimized || floatingView == null || layoutParams == null) {
            return;
        }
        if ((layoutParams.flags & WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE) != 0) {
            layoutParams.flags &= ~WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
            try {
                windowManager.updateViewLayout(floatingView, layoutParams);
            } catch (Exception ignored) {
            }
        }
        inputFocusActive = true;
        // 主动弹出键盘（textarea 已由 WebView 内部触摸聚焦）
        android.view.inputmethod.InputMethodManager imm =
                (android.view.inputmethod.InputMethodManager) getSystemService(INPUT_METHOD_SERVICE);
        if (imm != null && webView != null) {
            imm.showSoftInput(webView, android.view.inputmethod.InputMethodManager.SHOW_IMPLICIT);
        }
    }

    private void applyNotFocusable() {
        if (floatingView == null || layoutParams == null) {
            return;
        }
        if ((layoutParams.flags & WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE) == 0) {
            layoutParams.flags |= WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE;
            try {
                windowManager.updateViewLayout(floatingView, layoutParams);
            } catch (Exception ignored) {
            }
        }
    }

    private void minimize() {
        if (minimized || floatingView == null || layoutParams == null) {
            return;
        }
        minimized = true;
        inputFocusActive = false;
        applyNotFocusable();
        floatingView.findViewById(R.id.full_view).setVisibility(View.GONE);
        floatingView.findViewById(R.id.mini_view).setVisibility(View.VISIBLE);
        int miniPx = (int) (MINI_SIZE_DP * getResources().getDisplayMetrics().density);
        layoutParams.width = miniPx;
        layoutParams.height = miniPx;
        try {
            windowManager.updateViewLayout(floatingView, layoutParams);
        } catch (Exception ignored) {
        }
    }

    private void restore() {
        if (!minimized || floatingView == null || layoutParams == null) {
            return;
        }
        minimized = false;
        floatingView.findViewById(R.id.full_view).setVisibility(View.VISIBLE);
        floatingView.findViewById(R.id.mini_view).setVisibility(View.GONE);
        layoutParams.width = fullWidth;
        layoutParams.height = fullHeight;
        try {
            windowManager.updateViewLayout(floatingView, layoutParams);
        } catch (Exception ignored) {
        }
    }

    private final class DragTouchListener implements View.OnTouchListener {
        private int initialX;
        private int initialY;
        private float touchX;
        private float touchY;

        @Override
        public boolean onTouch(View v, MotionEvent event) {
            switch (event.getAction()) {
                case MotionEvent.ACTION_DOWN:
                    initialX = layoutParams.x;
                    initialY = layoutParams.y;
                    touchX = event.getRawX();
                    touchY = event.getRawY();
                    return true;
                case MotionEvent.ACTION_MOVE:
                    layoutParams.x = initialX + (int) (event.getRawX() - touchX);
                    layoutParams.y = initialY + (int) (event.getRawY() - touchY);
                    try {
                        windowManager.updateViewLayout(floatingView, layoutParams);
                    } catch (Exception ignored) {
                    }
                    return true;
                default:
                    return false;
            }
        }
    }

    private void stopFloating() {
        sActive = false;
        stopScreenshotObserver();
        stopCameraAutoGlance();
        if (sMainActivityRef != null) {
            sMainActivityRef.onFloatingStopped();
        }
        if (added && floatingView != null) {
            try {
                windowManager.removeView(floatingView);
            } catch (Exception ignored) {
            }
            added = false;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE);
        } else {
            stopForeground(true);
        }
        stopSelf();
    }

    @Override
    public void onDestroy() {
        sActive = false;
        stopScreenshotObserver();
        stopCameraAutoGlance();
        if (added && floatingView != null) {
            try {
                windowManager.removeView(floatingView);
            } catch (Exception ignored) {
            }
            added = false;
        }
        if (webView != null) {
            webView.destroy();
            webView = null;
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}

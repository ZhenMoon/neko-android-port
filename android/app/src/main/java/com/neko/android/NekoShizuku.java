package com.neko.android;

import android.content.ComponentName;
import android.content.ServiceConnection;
import android.content.pm.PackageManager;
import android.os.IBinder;
import android.os.RemoteException;
import android.util.Base64;

import org.json.JSONObject;

import rikka.shizuku.Shizuku;

/**
 * Shizuku-backed shell execution (adb commands without root / PC).
 *
 * The user must install Shizuku (moe.shizuku.manager), start its service
 * (via adb / root) and grant this app permission once. Commands are executed
 * inside the {@link NekoShellService} UserService, which runs with the shell
 * (uid 2000) or root (uid 0) identity.
 *
 * Safety: only a whitelisted command set is allowed, and shell metacharacters
 * (; | > < & $( `) are rejected so a single command can never be chained into
 * arbitrary code.
 */
public final class NekoShizuku {

    public static final int REQUEST_CODE = 4001;
    private static final String SHELL_SERVICE_CLASS =
            "com.neko.android.NekoShellService";

    private NekoShizuku() {
    }

    private static volatile INekoShell sShell;
    private static volatile boolean sBinding;

    // ---- 状态 ----

    /** Shizuku 服务可用（Manager 在运行）。 */
    public static boolean isServiceAvailable() {
        try {
            return Shizuku.pingBinder();
        } catch (Exception e) {
            return false;
        }
    }

    /** 已获得用户授权。 */
    public static boolean isPermissionGranted() {
        try {
            return Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED;
        } catch (Exception e) {
            return false;
        }
    }

    /** UserService（shell 身份进程）已绑定。 */
    public static boolean isShellBound() {
        return sShell != null;
    }

    public static boolean isReady() {
        return isServiceAvailable() && isPermissionGranted();
    }

    /** 请求授权（结果经 Shizuku.OnRequestPermissionResultListener 回调）。 */
    public static void requestPermission() {
        try {
            Shizuku.requestPermission(REQUEST_CODE);
        } catch (Exception ignored) {
        }
    }

    private static final ServiceConnection CONNECTION =
            new ServiceConnection() {
                @Override
                public void onServiceConnected(ComponentName componentName, IBinder service) {
                    sShell = INekoShell.Stub.asInterface(service);
                    sBinding = false;
                }

                @Override
                public void onServiceDisconnected(ComponentName componentName) {
                    sShell = null;
                    sBinding = false;
                }
            };

    /** 预热 UserService 绑定（授权后调用）。幂等。 */
    public static void ensureShellBound() {
        if (sShell != null || sBinding || !isReady()) {
            return;
        }
        sBinding = true;
        try {
            Shizuku.UserServiceArgs args = new Shizuku.UserServiceArgs(
                    new ComponentName("com.neko.android", SHELL_SERVICE_CLASS))
                    .daemon(false)
                    .processNameSuffix("shell")
                    .debuggable(false)
                    .version(1);
            Shizuku.bindUserService(args, CONNECTION);
        } catch (Exception e) {
            sBinding = false;
        }
    }

    // ---- 白名单命令校验 ----

    /** 危险 shell 元字符：命令拼接 / 注入向量，一律拒绝。 */
    private static final char[] DANGEROUS = {';', '|', '>', '<', '&', '$', '`', '\n', '\r'};

    private static final String[][] WHITELIST = {
            // 免权限截屏（输出到 stdout，二进制 PNG，由 service base64 编码）
            {"screencap", "-p"},
            // 模拟输入
            {"input", "tap"},
            {"input", "swipe"},
            {"input", "keyevent"},
            {"input", "text"},
            {"input", "motionevent"},
            // 系统设置读取 / 写入
            {"settings", "get"},
            {"settings", "put"},
            {"settings", "delete"},
            // 窗口 / 显示
            {"wm", "size"},
            {"wm", "density"},
            {"dumpsys", "window"},
            {"dumpsys", "activity"},
            {"getprop"},
            // 包管理（仅授权 / 撤销自身权限）
            {"pm", "grant", "com.neko.android"},
            {"pm", "revoke", "com.neko.android"},
    };

    /** 校验命令是否属于白名单。返回 null 表示通过；否则返回拒绝原因。 */
    public static String validateCommand(String command) {
        if (command == null) {
            return "empty command";
        }
        command = command.trim();
        if (command.isEmpty()) {
            return "empty command";
        }
        if (command.length() > 600) {
            return "command too long";
        }
        for (char c : DANGEROUS) {
            if (command.indexOf(c) >= 0) {
                return "shell metacharacter not allowed: " + c;
            }
        }
        String[] parts = command.split("\\s+");
        for (String[] allowed : WHITELIST) {
            if (matches(parts, allowed)) {
                return null;
            }
        }
        return "command not in whitelist";
    }

    private static boolean matches(String[] parts, String[] allowed) {
        if (parts.length < allowed.length) {
            return false;
        }
        for (int i = 0; i < allowed.length; i++) {
            if (!parts[i].equals(allowed[i])) {
                return false;
            }
        }
        return true;
    }

    // ---- 执行 ----

    /** 执行白名单 shell 命令，返回 JSON：{ok, code, stdout, stderr}。 */
    public static String execShellCommand(String command) {
        JSONObject result = new JSONObject();
        try {
            String reason = validateCommand(command);
            if (reason != null) {
                result.put("ok", false);
                result.put("error", reason);
                return result.toString();
            }
            if (!isReady()) {
                result.put("ok", false);
                result.put("error", "shizuku_not_ready");
                result.put("service", isServiceAvailable());
                result.put("granted", isPermissionGranted());
                return result.toString();
            }
            ensureShellBound();
            if (sShell == null) {
                result.put("ok", false);
                result.put("error", "shizuku_shell_not_bound");
                return result.toString();
            }
            return sShell.execCommand(command);
        } catch (RemoteException e) {
            try {
                result.put("ok", false);
                result.put("error", "remote_error: " + e);
            } catch (Exception ignored) {
            }
            return result.toString();
        } catch (Exception e) {
            try {
                result.put("ok", false);
                result.put("error", String.valueOf(e.getMessage()));
            } catch (Exception ignored) {
            }
            return result.toString();
        }
    }

    /** 免权限截屏：screencap -p 输出到 stdout，返回 base64 PNG（失败返回 null）。 */
    public static String screencapBase64() {
        String json = execShellCommand("screencap -p");
        try {
            JSONObject o = new JSONObject(json);
            if (!o.optBoolean("ok", false)) {
                return null;
            }
            String stdout = o.optString("stdout");
            return stdout.isEmpty() ? null : stdout;
        } catch (Exception e) {
            return null;
        }
    }

    static String encodeBase64(byte[] bytes) {
        return Base64.encodeToString(bytes, Base64.NO_WRAP);
    }
}

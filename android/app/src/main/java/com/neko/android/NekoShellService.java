package com.neko.android;

import android.os.RemoteException;

import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.util.concurrent.TimeUnit;

/**
 * Shizuku UserService running with shell (ADB) or root identity.
 *
 * Shizuku 13+ removed the old ``Shizuku.newProcess``; the official way to run
 * code with privilege is a UserService: a bound service instantiated inside
 * the Shizuku server process, where {@link Runtime#exec} inherits the shell
 * (uid 2000) or root (uid 0) identity. Commands are re-validated against the
 * same whitelist as the app side (defense in depth).
 */
public class NekoShellService extends INekoShell.Stub {

    private static final long TIMEOUT_SECONDS = 20;

    @Override
    public String execCommand(String command) throws RemoteException {
        JSONObject result = new JSONObject();
        try {
            String reason = NekoShizuku.validateCommand(command);
            if (reason != null) {
                result.put("ok", false);
                result.put("error", reason);
                return result.toString();
            }
            Process p = Runtime.getRuntime().exec(new String[]{"sh", "-c", command});
            byte[] outBytes = readAll(p.getInputStream());
            byte[] errBytes = readAll(p.getErrorStream());
            boolean done = p.waitFor(TIMEOUT_SECONDS, TimeUnit.SECONDS);
            if (!done) {
                p.destroy();
                result.put("ok", false);
                result.put("error", "command timeout");
                return result.toString();
            }
            result.put("ok", p.exitValue() == 0);
            result.put("code", p.exitValue());
            result.put("stdout", NekoShizuku.encodeBase64(outBytes));
            result.put("stderr", new String(errBytes, "UTF-8"));
        } catch (Exception e) {
            try {
                result.put("ok", false);
                result.put("error", String.valueOf(e.getMessage()));
            } catch (Exception ignored) {
            }
        }
        return result.toString();
    }

    private static byte[] readAll(InputStream in) throws Exception {
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        byte[] buf = new byte[8192];
        int n;
        while ((n = in.read(buf)) != -1) {
            out.write(buf, 0, n);
        }
        return out.toByteArray();
    }
}

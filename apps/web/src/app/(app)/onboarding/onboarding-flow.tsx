"use client";

import {
  Bell,
  BellSlash,
  DeviceMobile,
  Export,
  PaperPlaneTilt,
  PlusSquare,
} from "@phosphor-icons/react";
import { useEffect, useState, useSyncExternalStore } from "react";

import { StatusChip, type LegStatus } from "@/components/ui/badges";
import { button } from "@/components/ui/button";
import type { PushSummary } from "@/lib/push/send";

import {
  getDeviceState,
  removeSubscription,
  saveSubscription,
  sendTestPush,
  type DeviceState,
} from "./actions";

const PUBLIC_KEY = process.env.NEXT_PUBLIC_VAPID_PUBLIC_KEY ?? "";

// ------------------------------------------------------------ environment

type Platform = "ios" | "android" | "desktop";
type Env = { platform: Platform; standalone: boolean; push: boolean };

let cachedEnv: Env | null = null;
function readEnv(): Env {
  if (!cachedEnv) {
    const ua = navigator.userAgent;
    // iPadOS reports itself as a Mac; touch support gives it away.
    const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
    const standalone =
      window.matchMedia("(display-mode: standalone)").matches ||
      (navigator as Navigator & { standalone?: boolean }).standalone === true;
    cachedEnv = {
      platform: ios ? "ios" : /Android/.test(ua) ? "android" : "desktop",
      standalone,
      push: "serviceWorker" in navigator && "PushManager" in window && "Notification" in window,
    };
  }
  return cachedEnv;
}
const never = () => () => {};
/** null during server render and hydration; the real environment after. */
const useEnv = () => useSyncExternalStore(never, readEnv, () => null);

type Permission = NotificationPermission | "unsupported";
const readPermission = (): Permission =>
  "Notification" in window ? Notification.permission : "unsupported";
function subscribePermission(onChange: () => void) {
  let status: PermissionStatus | null = null;
  navigator.permissions
    ?.query({ name: "notifications" as PermissionName })
    .then((s) => {
      status = s;
      s.onchange = onChange;
    })
    .catch(() => undefined);
  return () => {
    if (status) status.onchange = null;
  };
}
const usePermission = () =>
  useSyncExternalStore(subscribePermission, readPermission, () => "unsupported" as Permission);

function applicationServerKey(base64url: string): Uint8Array<ArrayBuffer> {
  const base64 = (base64url + "=".repeat((4 - (base64url.length % 4)) % 4))
    .replace(/-/g, "+")
    .replace(/_/g, "/");
  const raw = atob(base64);
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

const reason = (e: unknown) => (e instanceof Error ? e.message : "unknown error");

// ------------------------------------------------------------------- view

function StepCard({
  step,
  title,
  status,
  statusLabel,
  children,
}: {
  step: number;
  title: string;
  status: LegStatus;
  statusLabel: string;
  children: React.ReactNode;
}) {
  return (
    <section
      aria-labelledby={`step-${step}`}
      className="flex flex-col rounded-card border border-border bg-surface p-6 lg:p-8"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="label-mono text-text-muted">Step {step} of 3</span>
        <StatusChip status={status} label={statusLabel} />
      </div>
      <h2 id={`step-${step}`} className="mt-5 font-display text-3xl text-text text-balance">
        {title}
      </h2>
      <div className="mt-3 flex grow flex-col gap-4 text-base text-text-muted">{children}</div>
    </section>
  );
}

export function OnboardingFlow() {
  const env = useEnv();
  const permission = usePermission();
  const [device, setDevice] = useState<{ endpoint: string | null; state: DeviceState } | null>(null);
  const [swMissing, setSwMissing] = useState(false);
  const [busy, setBusy] = useState<null | "enable" | "disable" | "test">(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [, rerender] = useState(0);

  const canPush = !!env?.push && !(env.platform === "ios" && !env.standalone);

  useEffect(() => {
    if (!canPush) return;
    let live = true;
    const timer = setTimeout(() => live && setSwMissing(true), 5000);
    (async () => {
      const reg = await navigator.serviceWorker.ready;
      clearTimeout(timer);
      const sub = await reg.pushManager.getSubscription();
      const r = await getDeviceState(sub?.endpoint ?? null);
      if (!r.ok) throw new Error(r.error);
      if (live) setDevice({ endpoint: sub?.endpoint ?? null, state: r.state });
    })().catch((e) => live && setNotice(`Couldn't read this device: ${reason(e)}`));
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [canPush]);

  async function enable() {
    setBusy("enable");
    setNotice(null);
    try {
      // First thing in the tap handler: iOS only shows the prompt from a
      // direct user gesture, so nothing may be awaited before it.
      const result =
        Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
      rerender((n) => n + 1);
      if (result !== "granted") return;
      if (!PUBLIC_KEY) throw new Error("the VAPID public key is not configured");
      const reg = await navigator.serviceWorker.ready;
      const sub =
        (await reg.pushManager.getSubscription()) ??
        (await reg.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: applicationServerKey(PUBLIC_KEY),
        }));
      const keys = sub.toJSON().keys ?? {};
      const r = await saveSubscription({
        endpoint: sub.endpoint,
        p256dh: keys.p256dh ?? "",
        auth: keys.auth ?? "",
        standalone: !!env?.standalone,
      });
      if (!r.ok) throw new Error(r.error);
      setDevice({ endpoint: sub.endpoint, state: r.state });
    } catch (e) {
      setNotice(`Couldn't turn on notifications: ${reason(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function disable() {
    setBusy("disable");
    setNotice(null);
    try {
      const reg = await navigator.serviceWorker.ready;
      const sub = await reg.pushManager.getSubscription();
      const endpoint = sub?.endpoint ?? device?.endpoint;
      await sub?.unsubscribe();
      const r = endpoint ? await removeSubscription(endpoint) : null;
      if (r && !r.ok) throw new Error(r.error);
      setDevice({ endpoint: null, state: r ? r.state : { subscribed: false, label: null } });
    } catch (e) {
      setNotice(`Couldn't turn off notifications: ${reason(e)}`);
    } finally {
      setBusy(null);
    }
  }

  async function test() {
    setBusy("test");
    setNotice(null);
    try {
      const result = await sendTestPush();
      if (!result.ok) throw new Error(result.error);
      const r: PushSummary = result.summary;
      setNotice(
        r.status === "no_devices"
          ? "No device has notifications on yet."
          : r.status === "sent"
            ? `Sent to ${r.total === 1 ? "this device" : `all ${r.total} devices`}. It should arrive within a few seconds, even with the phone locked.`
            : `Delivered to ${r.delivered} of ${r.total} devices. Devices that no longer exist were turned off.`,
      );
    } catch (e) {
      setNotice(`Couldn't send the test: ${reason(e)}`);
    } finally {
      setBusy(null);
    }
  }

  if (!env) {
    return <p className="label-mono text-text-muted">Checking this device…</p>;
  }

  // Desktop: say it once and stop. Install and push are set up on the phone.
  if (env.platform === "desktop") {
    return (
      <section className="max-w-2xl rounded-card border border-border bg-surface p-6 lg:p-8">
        <span className="label-mono text-text-muted">This device</span>
        <h2 className="mt-5 flex items-center gap-3 font-display text-3xl text-text">
          <DeviceMobile size={28} aria-hidden className="text-accent" />
          Set this up on your phone
        </h2>
        <p className="mt-3 max-w-measure text-base text-text-muted">
          Installing the app and turning on notifications happen on your iPhone. Open{" "}
          <span className="font-mono text-text">valemont-command.vercel.app/onboarding</span> in
          Safari there.
        </p>
      </section>
    );
  }

  const installed = env.standalone;
  const subscribed = !!device?.state.subscribed;

  // ---- Step 1: install
  const install = (
    <StepCard
      step={1}
      title={installed ? "Installed" : env.platform === "ios" ? "Add to Home Screen" : "Install the app"}
      status={installed ? "hit" : env.platform === "ios" ? "on-pace" : "dead"}
      statusLabel={installed ? "Done" : env.platform === "ios" ? "Next" : "Optional"}
    >
      {installed ? (
        <p>You&apos;re in the Home Screen app.</p>
      ) : env.platform === "ios" ? (
        <>
          <ol className="flex flex-col gap-3">
            <li className="flex items-center gap-3 text-text">
              <Export size={22} aria-hidden className="shrink-0 text-accent" />
              Tap Share in Safari&apos;s toolbar.
            </li>
            <li className="flex items-center gap-3 text-text">
              <PlusSquare size={22} aria-hidden className="shrink-0 text-accent" />
              Choose Add to Home Screen, then Add.
            </li>
            <li className="flex items-center gap-3 text-text">
              <DeviceMobile size={22} aria-hidden className="shrink-0 text-accent" />
              Open Valemont from your Home Screen and come back here.
            </li>
          </ol>
          <p className="text-sm">
            Needs iOS 16.4 or later. iPhone only allows notifications in the Home Screen app, not
            in a Safari tab.
          </p>
        </>
      ) : (
        <p>
          Optional on Android: browser menu, then Install app. Notifications also work in Chrome
          without installing.
        </p>
      )}
    </StepCard>
  );

  // ---- Step 2: notifications
  let notifyStatus: LegStatus = "on-pace";
  let notifyLabel = "Next";
  let notifyBody: React.ReactNode;
  if (env.platform === "ios" && !installed) {
    notifyStatus = "dead";
    notifyLabel = "After step 1";
    notifyBody = <p>Open the Home Screen app first. This step unlocks there.</p>;
  } else if (!env.push || permission === "unsupported") {
    notifyStatus = "dead";
    notifyLabel = "Unsupported";
    notifyBody = <p>This browser can&apos;t receive push notifications.</p>;
  } else if (permission === "denied") {
    notifyStatus = "danger";
    notifyLabel = "Blocked";
    notifyBody =
      env.platform === "ios" ? (
        <p>
          Notifications are off for Valemont. Open iPhone Settings, then Notifications, then
          Valemont, and turn on Allow Notifications. Then reopen the app.
        </p>
      ) : (
        <p>
          Notifications are blocked for this site. Allow them in the browser&apos;s site settings,
          then reload this page.
        </p>
      );
  } else if (swMissing && !device) {
    notifyStatus = "dead";
    notifyLabel = "Unavailable";
    notifyBody = <p>The service worker isn&apos;t running, so this device can&apos;t subscribe.</p>;
  } else if (!device) {
    notifyLabel = "Checking";
    notifyBody = <p className="label-mono">Checking this device…</p>;
  } else if (subscribed) {
    notifyStatus = "hit";
    notifyLabel = "On";
    notifyBody = (
      <>
        <p>
          On for <span className="text-text">{device.state.label ?? "this device"}</span>.
        </p>
        <button
          type="button"
          onClick={disable}
          disabled={busy !== null}
          className={`${button.secondary} self-start`}
        >
          <BellSlash size={18} aria-hidden />
          {busy === "disable" ? "Turning off…" : "Turn off for this device"}
        </button>
      </>
    );
  } else {
    notifyBody = (
      <>
        <p>
          {permission === "granted"
            ? "Permission is already granted. Tap to subscribe this device."
            : "Your phone asks once, and only when you tap this."}
        </p>
        <button
          type="button"
          onClick={enable}
          disabled={busy !== null}
          className={`${button.primary} self-start`}
        >
          <Bell size={18} aria-hidden />
          {busy === "enable" ? "Turning on…" : "Enable notifications"}
        </button>
      </>
    );
  }

  return (
    <>
      <div className="grid gap-4 lg:grid-cols-3">
        {install}
        <StepCard step={2} title="Notifications" status={notifyStatus} statusLabel={notifyLabel}>
          {notifyBody}
        </StepCard>
        <StepCard
          step={3}
          title="Send a test"
          status={subscribed ? "on-pace" : "dead"}
          statusLabel={subscribed ? "Ready" : "After step 2"}
        >
          <p>Sends a push to every device with notifications on. Lock the phone to check it arrives.</p>
          <button
            type="button"
            onClick={test}
            disabled={!subscribed || busy !== null}
            className={`${button.strong} self-start`}
          >
            <PaperPlaneTilt size={18} aria-hidden />
            {busy === "test" ? "Sending…" : "Send test push"}
          </button>
        </StepCard>
      </div>
      <p role="status" aria-live="polite" className="mt-6 min-h-6 max-w-measure text-sm text-text">
        {notice}
      </p>
    </>
  );
}

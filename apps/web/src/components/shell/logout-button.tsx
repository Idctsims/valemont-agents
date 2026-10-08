import { SignOut } from "@phosphor-icons/react/ssr";

import { logout } from "@/app/login/actions";

/** Labelled on the desktop rail; a full-width row inside the phone menu. */
export function LogoutButton({ menuItem = false }: { menuItem?: boolean }) {
  return (
    <form action={logout}>
      <button
        type="submit"
        className={
          menuItem
            ? "tap flex w-full items-center gap-3 rounded-inner px-3 text-sm text-text transition-colors hover:bg-surface-2"
            : "tap inline-flex items-center justify-center gap-2 rounded-pill px-3 text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
        }
      >
        <SignOut size={20} aria-hidden />
        <span className="text-sm">Log out</span>
      </button>
    </form>
  );
}

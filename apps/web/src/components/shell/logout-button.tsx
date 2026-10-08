import { SignOut } from "@phosphor-icons/react/ssr";

import { logout } from "@/app/login/actions";

export function LogoutButton({ withLabel = false }: { withLabel?: boolean }) {
  return (
    <form action={logout}>
      <button
        type="submit"
        aria-label={withLabel ? undefined : "Log out"}
        className="tap inline-flex items-center justify-center gap-2 rounded-pill px-3 text-text-muted transition-colors hover:bg-surface-2 hover:text-text"
      >
        <SignOut size={20} aria-hidden />
        {withLabel && <span className="text-sm">Log out</span>}
      </button>
    </form>
  );
}

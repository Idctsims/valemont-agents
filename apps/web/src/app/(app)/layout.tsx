import { requireOwner } from "@/lib/auth";

import { logout } from "../login/actions";

export default async function AppLayout({ children }: LayoutProps<"/">) {
  await requireOwner();

  return (
    <>
      <form action={logout}>
        <button type="submit">Log out</button>
      </form>
      {children}
    </>
  );
}

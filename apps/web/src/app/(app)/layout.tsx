import { cookies } from "next/headers";

import { AppShell } from "@/components/shell/app-shell";
import { requireOwner } from "@/lib/auth";
import { THEME_COOKIE, parseTheme } from "@/lib/theme";

export default async function AppLayout({ children }: LayoutProps<"/">) {
  await requireOwner();
  const theme = parseTheme((await cookies()).get(THEME_COOKIE)?.value);

  return <AppShell theme={theme}>{children}</AppShell>;
}

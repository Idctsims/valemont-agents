import type { Metadata } from "next";

import { PageHeader } from "@/components/ui/page-header";

import { OnboardingFlow } from "./onboarding-flow";

export const metadata: Metadata = { title: "Phone setup" };

export default function OnboardingPage() {
  return (
    <>
      <PageHeader
        label="This device"
        title="Set up your phone"
        summary="Install Valemont on your Home Screen and turn on notifications, so alerts reach you with the phone locked."
      />
      <OnboardingFlow />
    </>
  );
}

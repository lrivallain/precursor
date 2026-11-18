import { McpAuthBanner } from "./McpAuthBanner";
import { TelemetryInvite } from "./TelemetryInvite";

/** The app-wide banners shown above every section. */
export function AppBanners() {
  return (
    <>
      <McpAuthBanner />
      <TelemetryInvite />
    </>
  );
}

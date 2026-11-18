# Telemetry backend on Azure

What the maintainers (or a fork that wants its own numbers) set up in Azure
to receive Precursor's [anonymous usage data](../website/features/telemetry.md).
The client posts JSON envelopes straight to Application Insights' ingestion
endpoint (`/v2/track`) with the connection string. There is no SDK, no Function
and no collector, so every guard lives in Azure: a daily cap, a short
retention, and an ingestion-time transformation that drops anything that is
not a well-formed Precursor envelope.

## What the client sends

| Item | Table | Recognised by |
| --- | --- | --- |
| Daily heartbeat | `AppEvents` | `SDKVersion == "precursor-telemetry:1"`, `Name == "heartbeat"` |
| Error report | `AppExceptions` | `SDKVersion == "precursor-telemetry:1"`, `ProblemId` = 16 hex chars, `OuterMessage == ExceptionType` |

Common tags: `UserId` (random UUID per install), `AppVersion` (CalVer
`YYYY.M.MICRO`, dev builds carry a `.devN+g<sha>` suffix), `AppRoleName ==
"precursor"`. Heartbeat `Properties` keys: `schema`, `version`, `channel`,
`install_mode`, `os`, `arch`, `python`, `llm_provider`, `agents`, `live`, `iq`,
`backup`, `mcp_servers`, `plugins`, `messages_24h`, `agent_runs_24h`, `topics`,
`chats` (all strings). Error `Properties`: `logger`, `fingerprint`.

## Checklist

1. **Resource group** in an EU region (for example `francecentral` or
   `westeurope`), e.g. `rg-precursor-telemetry`.
2. **Log Analytics workspace**, e.g. `log-precursor-telemetry`:
   - pricing tier Pay-as-you-go;
   - **daily cap** `workspaceCapping.dailyQuotaGb = 0.1` (about 100 MB a day,
     several orders of magnitude above the expected volume);
   - retention 90 days on `AppEvents` and `AppExceptions`, 30 days workspace
     default.
3. **Application Insights**, workspace-based, e.g. `appi-precursor-telemetry`:
   - `kind: other`, `Application_Type: other`, `IngestionMode: LogAnalytics`,
     `WorkspaceResourceId` = the workspace above;
   - **keep local authentication enabled** (`DisableLocalAuth: false`): the
     client authenticates with the instrumentation key only, an Entra-only
     resource refuses it;
   - keep IP masking on (`DisableIpMasking: false`, the default);
   - public network access for ingestion enabled, for query as you prefer;
   - its own **daily cap** at 0.1 GB/day too, with the cap warning email on;
   - sampling off (nothing to sample), Smart Detection, Profiler and Snapshot
     Debugger off.
4. **Workspace transformation DCR** (`kind: WorkspaceTransforms`, attached to
   the workspace as its `defaultDataCollectionRuleResourceId`) with two data
   flows. Check that `AppEvents` and `AppExceptions` are on the list of tables
   that support transformations in the target region before relying on it;
   validate the KQL in the portal's transformation editor (it only accepts the
   supported subset of KQL; use `pack` if `bag_pack` is refused).

   `Microsoft-Table-AppEvents`:

   ```kusto
   source
   | where SDKVersion == "precursor-telemetry:1" and Name == "heartbeat"
   | where AppRoleName == "precursor"
   | where UserId matches regex "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
   | where AppVersion matches regex "^20[0-9]{2}\\.[0-9]{1,2}\\.[0-9]+"
   | where strlen(tostring(Properties)) < 2048
   | extend Properties = bag_pack(
       "schema", tostring(Properties.schema),
       "version", tostring(Properties.version),
       "channel", tostring(Properties.channel),
       "install_mode", tostring(Properties.install_mode),
       "os", tostring(Properties.os),
       "arch", tostring(Properties.arch),
       "python", tostring(Properties.python),
       "llm_provider", tostring(Properties.llm_provider),
       "agents", tostring(Properties.agents),
       "live", tostring(Properties.live),
       "iq", tostring(Properties.iq),
       "backup", tostring(Properties.backup),
       "mcp_servers", tostring(Properties.mcp_servers),
       "plugins", tostring(Properties.plugins),
       "messages_24h", tostring(Properties.messages_24h),
       "agent_runs_24h", tostring(Properties.agent_runs_24h),
       "topics", tostring(Properties.topics),
       "chats", tostring(Properties.chats))
   | extend Measurements = dynamic({})
   | project-away ClientIP, ClientCity, ClientStateOrProvince
   ```

   `Microsoft-Table-AppExceptions`:

   ```kusto
   source
   | where SDKVersion == "precursor-telemetry:1" and AppRoleName == "precursor"
   | where ProblemId matches regex "^[0-9a-f]{16}$"
   // The client never sends the real message: anything else is not ours.
   | where OuterMessage == ExceptionType
   | where UserId matches regex "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
   | where strlen(tostring(Details)) < 16384
   | extend Properties = bag_pack(
       "logger", tostring(Properties.logger),
       "fingerprint", tostring(Properties.fingerprint))
   | project-away ClientIP, ClientCity, ClientStateOrProvince
   ```

5. **Alerts** (one action group, email to the maintainers):
   - the workspace daily cap was reached:
     `_LogOperation | where Category == "Ingestion" and Operation has "Data collection stopped"`;
   - an ingestion spike: `AppEvents | where TimeGenerated > ago(1h) | count`
     above, say, 10x the usual hourly heartbeat count, checked hourly.
6. **Workbook** (shared, in the resource group) with these queries:

   ```kusto
   // Active installs per day (an install counts once a day)
   AppEvents | where Name == "heartbeat"
   | summarize installs = dcount(UserId) by bin(TimeGenerated, 1d)

   // Monthly active installs, seen on at least two different days (filters one-off noise)
   AppEvents | where Name == "heartbeat" and TimeGenerated > ago(30d)
   | summarize days = dcount(bin(TimeGenerated, 1d)) by UserId
   | where days >= 2 | count

   // Versions in use (latest heartbeat per install, last 7 days)
   AppEvents | where Name == "heartbeat" and TimeGenerated > ago(7d)
   | summarize arg_max(TimeGenerated, AppVersion) by UserId
   | summarize installs = count() by AppVersion | order by installs desc

   // OS, install type and channel
   AppEvents | where Name == "heartbeat" and TimeGenerated > ago(7d)
   | summarize arg_max(TimeGenerated, Properties) by UserId
   | summarize installs = count() by os = tostring(Properties.os),
       install_mode = tostring(Properties.install_mode), channel = tostring(Properties.channel)

   // Feature adoption
   AppEvents | where Name == "heartbeat" and TimeGenerated > ago(7d)
   | summarize arg_max(TimeGenerated, Properties) by UserId
   | summarize agents = countif(tostring(Properties.agents) == "true"),
       live = countif(tostring(Properties.live) == "true"),
       iq = countif(tostring(Properties.iq) == "true"), installs = count()

   // Top errors
   AppExceptions | where TimeGenerated > ago(7d)
   | summarize installs = dcount(UserId), reports = count(), last = max(TimeGenerated)
       by ProblemId, ExceptionType, AppVersion
   | order by installs desc
   ```

7. **Access**: Reader / Log Analytics Reader for the maintainers. Nothing in
   the client needs a secret: the connection string is write-only and public
   by design.

## What to hand back

- The Application Insights **connection string** (`InstrumentationKey=…;
  IngestionEndpoint=…`). It goes into `DEFAULT_TELEMETRY_CONNECTION_STRING` in
  `precursor/backend/config.py`; until then every build reports "no telemetry
  endpoint" and sends nothing.
- The workbook's URL, for the maintainers.

## Smoke test

Once the resource exists, post one heartbeat by hand (replace the key and
endpoint) and look for it in `AppEvents` a few minutes later:

```bash
curl -s https://<region>.in.applicationinsights.azure.com/v2/track \
  -H 'Content-Type: application/json' \
  -d '[{"name":"Microsoft.ApplicationInsights.Event","time":"2026-11-17T12:00:00.000Z",
        "iKey":"<instrumentation-key>",
        "tags":{"ai.internal.sdkVersion":"precursor-telemetry:1","ai.cloud.role":"precursor",
                "ai.user.id":"00000000-0000-4000-8000-000000000000","ai.application.ver":"2026.11.0"},
        "data":{"baseType":"EventData","baseData":{"ver":2,"name":"heartbeat",
                "properties":{"schema":"1","os":"darwin"}}}}]'
```

The response should read `"itemsAccepted":1`. A second post with another
`ai.internal.sdkVersion` should be accepted by the endpoint but never show up
in `AppEvents`: that proves the transformation is attached.

## Cost

Each heartbeat is under 1 KB. Ten thousand installs is about 300 MB a month,
well inside Log Analytics' free 5 GB a month per billing account; the daily
caps bound the worst case at about 3 GB a month per resource.

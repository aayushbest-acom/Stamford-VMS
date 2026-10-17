using System;
using System.Collections.Generic;
using System.Data;
using System.IO;
using System.Globalization;
using System.Text.RegularExpressions;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using Genetec.Sdk;
using Genetec.Sdk.Entities;
using Genetec.Sdk.Entities.AccessControl.Visitors;
using Genetec.Sdk.Queries;

namespace StamfordVms.GenetecReadOnlyBridge
{
    internal static class Program
    {
        private const string Prefix = "http://127.0.0.1:5190/";
        private const string BridgeBuild = "5.1.6";
        private const string TestWriteConfirmation = "CREATE_ONLY_STAMFORD_VMS_TEST_CARDHOLDER";
        private const string VisitorSyncConfirmation = "SYNC_SYNTHETIC_STAMFORD_VMS_VISITOR";
        private const string VisitorLifecycleConfirmation = "SYNC SYNTHETIC STAMFORD VMS VISITOR LIFECYCLE";
        private const string ArchivedCheckoutReconcileConfirmation = "RECONCILE VERIFIED ARCHIVED CHECKOUT TO LOCAL LEDGER";
        private static readonly object LifecycleLedgerLock = new object();
        private const string TestEntityKey = "STAMFORD VMS PROTOTYPE TEST CARDHOLDER";
        private const string TestFirstName = "Stamford VMS Prototype";
        private const string TestLastName = "Test Cardholder";
        private static readonly JavaScriptSerializer Serializer = new JavaScriptSerializer();
        private static string _sdkDirectory;

        private static int Main(string[] args)
        {
            _sdkDirectory = Environment.GetEnvironmentVariable("GSC_SDK");
            if (String.IsNullOrWhiteSpace(_sdkDirectory))
            {
                _sdkDirectory = @"C:\Program Files (x86)\Genetec Security Center 5.11 SDK";
            }

            // Load other Genetec SDK assemblies only from the configured local SDK directory.
            AppDomain.CurrentDomain.AssemblyResolve += ResolveSdkAssembly;

            try
            {
                using (var listener = new HttpListener())
                {
                    listener.Prefixes.Add(Prefix);
                    listener.Start();
                    Console.WriteLine("Stamford VMS Genetec bridge build " + BridgeBuild + " is listening on " + Prefix);
                    Console.WriteLine("Endpoints: GET /api/health, GET /api/genetec/status, GET /api/genetec/capabilities");
                    Console.WriteLine("Read-only Visitor key lookup: GET /api/genetec/visitor-booking-lookup?bookingId=VMS-SDKTEST-01");
                    Console.WriteLine("Read-only lifecycle state inspection: GET /api/genetec/visitor-lifecycle-state?bookingId=VMS-SDKTEST-01");
                    Console.WriteLine("Guarded local-ledger reconciliation (no Genetec writes): POST /api/genetec/visitor-lifecycle-reconcile-checkout");
                    Console.WriteLine("Optional guarded write: POST /api/genetec/test-cardholder");
                    Console.WriteLine("Optional visitor booking sync: POST /api/genetec/visitor-booking (native Visitor entity; no credentials/access rights)");
                    Console.WriteLine("Optional visitor lifecycle sync: POST /api/genetec/visitor-lifecycle (synthetic visitors only; requires separate startup opt-in)");
                    Console.WriteLine(IsTestWriteEnabled()
                        ? "GUARDED TEST WRITE ENABLED: fixed prototype cardholder only; no credentials/access rights."
                        : "Fixed test-cardholder write remains disabled.");
                    Console.WriteLine(IsVisitorSyncEnabled()
                        ? "GUARDED VISITOR SYNC ENABLED: only VMS booking IDs in Invited state; no credentials/access rights."
                        : "Visitor booking sync remains disabled.");
                    Console.WriteLine(IsVisitorLifecycleSyncEnabled()
                        ? "GUARDED VISITOR LIFECYCLE ENABLED: native check-in/check-out candidate path for synthetic bookings only."
                        : "Visitor lifecycle sync remains disabled.");
                    Console.WriteLine("Press Ctrl+C to stop.");

                    while (listener.IsListening)
                    {
                        HttpListenerContext context;
                        try { context = listener.GetContext(); }
                        catch (HttpListenerException) { break; }
                        catch (ObjectDisposedException) { break; }
                        Handle(context);
                    }
                }
                return 0;
            }
            catch (HttpListenerException ex)
            {
                Console.Error.WriteLine("Unable to start the local HTTP listener. Check the URL ACL for " + Prefix);
                Console.Error.WriteLine(ex.Message);
                Console.Error.WriteLine("An administrator can register the URL with netsh http add urlacl url=" + Prefix + " user=" + Environment.UserDomainName + "\\" + Environment.UserName);
                return 2;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("Bridge startup failed: " + ex.GetType().Name + " — " + ex.Message);
                return 1;
            }
        }

        private static void Handle(HttpListenerContext context)
        {
            var request = context.Request;
            var response = context.Response;
            string origin = request.Headers["Origin"];
            bool allowedOrigin = String.IsNullOrEmpty(origin) ||
                String.Equals(origin, "http://127.0.0.1:8080", StringComparison.OrdinalIgnoreCase) ||
                String.Equals(origin, "http://localhost:8080", StringComparison.OrdinalIgnoreCase);
            if (!allowedOrigin)
            {
                response.StatusCode = 403;
                response.Close();
                return;
            }
            if (!String.IsNullOrEmpty(origin)) response.Headers["Access-Control-Allow-Origin"] = origin;
            response.Headers["Vary"] = "Origin";
            response.Headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
            response.Headers["Access-Control-Allow-Headers"] = "Content-Type";
            response.Headers["Cache-Control"] = "no-store";

            if (String.Equals(request.HttpMethod, "OPTIONS", StringComparison.OrdinalIgnoreCase))
            {
                response.StatusCode = 204;
                response.Close();
                return;
            }

            object payload;
            int statusCode;
            string path = request.Url == null ? String.Empty : request.Url.AbsolutePath.TrimEnd('/');

            if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/health", StringComparison.OrdinalIgnoreCase))
            {
                statusCode = 200;
                bool testCardholderWriteEnabled = IsTestWriteEnabled();
                bool visitorSyncEnabled = IsVisitorSyncEnabled();
                payload = new
                {
                    service = "Stamford VMS Genetec Bridge",
                    bridge = "online",
                    bridgeBuild = BridgeBuild,
                    bridgeExecutablePath = Assembly.GetExecutingAssembly().Location,
                    mode = testCardholderWriteEnabled || visitorSyncEnabled ? "read-only-with-separately-guarded-write-scopes" : "read-only",
                    sdkDirectoryConfigured = !String.IsNullOrWhiteSpace(_sdkDirectory) && System.IO.Directory.Exists(_sdkDirectory),
                    writeOperationsEnabled = testCardholderWriteEnabled || visitorSyncEnabled,
                    testCardholderWriteEnabled = testCardholderWriteEnabled,
                    visitorSyncEnabled = visitorSyncEnabled,
                    visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled(),
                    writeScope = testCardholderWriteEnabled && visitorSyncEnabled
                        ? "fixed-test-cardholder-and-invited-synthetic-native-visitor-only"
                        : (testCardholderWriteEnabled ? "one-fixed-test-cardholder-create-only" : (visitorSyncEnabled ? "invited-synthetic-native-visitor-create-update-only" : "disabled")),
                    lifecycleScope = IsVisitorLifecycleSyncEnabled() ? "synthetic-native-visitor-checkin-checkout-only" : "disabled"
                };
            }
            else if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/status", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    payload = ReadGenetecStatus();
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new
                    {
                        service = "Stamford VMS Genetec Bridge",
                        mode = "read-only",
                        connected = false,
                        errorCode = ex.Code,
                        error = ex.Message,
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeOperationsEnabled = IsTestWriteEnabled(),
                        writeScope = IsTestWriteEnabled() ? "one-fixed-test-cardholder-create-only" : "disabled",
                        visitorManagementLicensed = false,
                        visitorSyncEnabled = false,
                        visitorSyncScope = "disabled"
                    };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Status request failed: " + ex.GetType().Name + " — " + ex.Message);
                    statusCode = 503;
                    payload = new
                    {
                        service = "Stamford VMS Genetec Bridge",
                        mode = "read-only",
                        connected = false,
                        errorCode = "GENETEC_STATUS_FAILED",
                        error = "The bridge could not complete the read-only Genetec status check. Review local bridge logs.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeOperationsEnabled = IsTestWriteEnabled(),
                        writeScope = IsTestWriteEnabled() ? "one-fixed-test-cardholder-create-only" : "disabled",
                        visitorManagementLicensed = false,
                        visitorSyncEnabled = false,
                        visitorSyncScope = "disabled"
                    };
                }
            }
            else if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/capabilities", StringComparison.OrdinalIgnoreCase))
            {
                // Report implementation availability separately from successful end-to-end validation.
                // The first controlled non-production lifecycle run is what proves the mapping on this Directory.
                bool lifecycleSdkMembersAvailable = HasLifecycleSdkMembers();
                statusCode = 200;
                payload = new
                {
                    service = "Stamford VMS Genetec Bridge",
                    sdkVersion = typeof(Engine).Assembly.GetName().Version == null
                        ? "unknown" : typeof(Engine).Assembly.GetName().Version.ToString(),
                    nativeVisitorBookingSyncSupported = true,
                    nativeVisitorBookingSyncEnabled = IsVisitorSyncEnabled(),
                    visitorLifecycleSyncSupported = lifecycleSdkMembersAvailable,
                    visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled(),
                    lifecycleSdkMemberMappingAvailable = lifecycleSdkMembersAvailable,
                    lifecycleOperationMappingVerified = IsLifecycleOperationMappingVerified(),
                    visitorLifecycleSyncScope = lifecycleSdkMembersAvailable
                        ? "synthetic-native-visitor-activation-checkin-and-Visitor.Checkout-checkout-only"
                        : "disabled-sdk-member-check-failed",
                    message = IsLifecycleOperationMappingVerified()
                        ? "A non-production Visitor lifecycle round-trip has completed with independent read-back."
                        : "Lifecycle SDK members are available. End-to-end check-in/check-out read-back is not yet validated on this Directory; use only the gated synthetic test."
                };
            }
            else if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/visitor-booking-lookup", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    payload = LookupVmsVisitorByBookingId(request.QueryString["bookingId"]);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new
                    {
                        success = false,
                        mode = "read-only-visitor-booking-lookup",
                        errorCode = ex.Code,
                        error = ex.Message,
                        checkedAtUtc = DateTime.UtcNow.ToString("o")
                    };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Visitor booking lookup failed: " + ex.GetType().Name + " — " + ex.Message);
                    statusCode = 503;
                    payload = new
                    {
                        success = false,
                        mode = "read-only-visitor-booking-lookup",
                        errorCode = "VISITOR_BOOKING_LOOKUP_FAILED",
                        error = "The read-only booking lookup failed. Review bridge logs; no write was attempted.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o")
                    };
                }
            }
            else if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/visitor-lifecycle-state", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    payload = ReadVisitorLifecycleStateSnapshot(request.QueryString["bookingId"]);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new
                    {
                        success = false,
                        mode = "read-only-visitor-lifecycle-state",
                        errorCode = ex.Code,
                        error = ex.Message,
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeAttempted = false
                    };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Visitor lifecycle state inspection failed: " + ex.GetType().Name + " — " + ex.Message);
                    statusCode = 503;
                    payload = new
                    {
                        success = false,
                        mode = "read-only-visitor-lifecycle-state",
                        errorCode = "VISITOR_LIFECYCLE_STATE_INSPECTION_FAILED",
                        error = "Could not inspect the Visitor's active/archived state. No write was attempted; review local bridge logs.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeAttempted = false
                    };
                }
            }
            else if (String.Equals(request.HttpMethod, "POST", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/visitor-lifecycle-reconcile-checkout", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    VisitorLifecycleRequest body = ReadVisitorLifecycleRequest(request);
                    if (body == null || !String.Equals(body.Confirmation, ArchivedCheckoutReconcileConfirmation, StringComparison.Ordinal))
                    {
                        throw new BridgeException(400, "ARCHIVED_CHECKOUT_RECONCILE_CONFIRMATION_REQUIRED",
                            "The request must explicitly confirm reconciliation of one already-verified archived synthetic checkout into the local ledger. This endpoint does not write to Genetec.");
                    }
                    payload = ReconcileVerifiedArchivedCheckoutToLedger(body.BookingId);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new
                    {
                        success = false,
                        mode = "reconcile-verified-archived-checkout-to-local-ledger",
                        errorCode = ex.Code,
                        error = ex.Message,
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        genetecWriteAttempted = false
                    };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Archived checkout ledger reconciliation failed: " + ex.GetType().Name + " — " + ex.Message);
                    statusCode = 503;
                    payload = new
                    {
                        success = false,
                        mode = "reconcile-verified-archived-checkout-to-local-ledger",
                        errorCode = "ARCHIVED_CHECKOUT_RECONCILIATION_FAILED",
                        error = "Could not reconcile the local event ledger. No Genetec write was attempted; inspect the read-only state and bridge logs.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        genetecWriteAttempted = false
                    };
                }
            }
            else if (String.Equals(request.HttpMethod, "POST", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/visitor-booking", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    VisitorSyncRequest body = ReadVisitorSyncRequest(request);
                    if (body == null || !String.Equals(body.Confirmation, VisitorSyncConfirmation, StringComparison.Ordinal))
                    {
                        throw new BridgeException(400, "VISITOR_SYNC_CONFIRMATION_REQUIRED",
                            "The request must explicitly confirm synchronising one synthetic VMS appointment to Genetec.");
                    }

                    payload = CreateOrUpdateVmsVisitor(body);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new
                    {
                        success = false,
                        mode = "guarded-visitor-sync",
                        errorCode = ex.Code,
                        error = ex.Message,
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        visitorSyncEnabled = IsVisitorSyncEnabled()
                    };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Visitor booking sync failed: " + ex.GetType().Name + " — " + ex.Message);
                    statusCode = 503;
                    payload = new
                    {
                        success = false,
                        mode = "guarded-visitor-sync",
                        errorCode = "VISITOR_SYNC_FAILED",
                        error = "Visitor synchronisation did not complete. Review bridge logs and verify Genetec before retrying.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        visitorSyncEnabled = IsVisitorSyncEnabled()
                    };
                }
            }
            else if (String.Equals(request.HttpMethod, "POST", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/visitor-lifecycle", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    VisitorLifecycleRequest body = ReadVisitorLifecycleRequest(request);
                    if (body == null || !String.Equals(body.Confirmation, VisitorLifecycleConfirmation, StringComparison.Ordinal))
                    {
                        throw new BridgeException(400, "VISITOR_LIFECYCLE_CONFIRMATION_REQUIRED",
                            "The request must explicitly confirm one synthetic visitor lifecycle event.");
                    }
                    payload = ApplyVisitorLifecycleEvent(body);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new
                    {
                        success = false,
                        mode = "guarded-visitor-lifecycle",
                        errorCode = ex.Code,
                        error = ex.Message,
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled()
                    };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Native Visitor lifecycle operation failed: " + ex.GetType().Name + " — " + ex.Message);
                    statusCode = 503;
                    payload = new
                    {
                        success = false,
                        mode = "guarded-visitor-lifecycle",
                        errorCode = "VISITOR_LIFECYCLE_FAILED",
                        error = "The lifecycle operation did not complete or could not be verified. Inspect Security Center and bridge logs before retrying.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled()
                    };
                }
            }
            else if (String.Equals(request.HttpMethod, "POST", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/test-cardholder", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    WriteRequest body = ReadWriteRequest(request);
                    if (body == null || !String.Equals(body.Confirmation, TestWriteConfirmation, StringComparison.Ordinal))
                    {
                        throw new BridgeException(400, "WRITE_CONFIRMATION_REQUIRED",
                            "The request must explicitly confirm the create-only test-cardholder operation.");
                    }

                    payload = CreateOrFindTestCardholder();
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new
                    {
                        success = false,
                        mode = "guarded-test-write",
                        errorCode = ex.Code,
                        error = ex.Message,
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeOperationsEnabled = IsTestWriteEnabled()
                    };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Guarded test write failed: " + ex.GetType().Name + " — " + ex.Message);
                    statusCode = 503;
                    payload = new
                    {
                        success = false,
                        mode = "guarded-test-write",
                        errorCode = "TEST_CARDHOLDER_CREATE_FAILED",
                        error = "The test cardholder operation failed. Review the bridge console and verify the designated test environment.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeOperationsEnabled = IsTestWriteEnabled()
                    };
                }
            }
            else if (!String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                     !String.Equals(request.HttpMethod, "POST", StringComparison.OrdinalIgnoreCase))
            {
                statusCode = 405;
                payload = new { error = "method_not_allowed", writeOperationsEnabled = IsTestWriteEnabled() };
            }
            else
            {
                statusCode = 404;
                payload = new { error = "not_found", writeOperationsEnabled = IsTestWriteEnabled() };
            }

            WriteJson(response, statusCode, payload);
        }

        private static object ReadGenetecStatus()
        {
            using (var engine = ConnectToGenetec())
            {
                try
                {
                    var mainServer = engine.GetEntities(EntityType.Server)
                        .OfType<Server>()
                        .FirstOrDefault(s => s.IsMainServer);

                    if (mainServer == null)
                    {
                        throw new BridgeException(503, "DIRECTORY_SERVER_NOT_FOUND", "Authenticated, but the main Directory server entity was not found.");
                    }

                    var systemConfiguration = engine.GetEntity(SdkGuids.SystemConfiguration) as SystemConfiguration;
                    bool visitorManagementLicensed = systemConfiguration != null &&
                        systemConfiguration.License != null && systemConfiguration.License.IsVisitorManagementEnabled;

                    var query = engine.ReportManager.CreateReportQuery(ReportType.EntityConfiguration) as EntityConfigurationQuery;
                    if (query == null)
                    {
                        throw new BridgeException(503, "QUERY_UNAVAILABLE", "The SDK did not create an EntityConfigurationQuery.");
                    }

                    query.EntityTypeFilter.Add(EntityType.CardholderGroup);
                    var queryResult = query.Query();
                    if (queryResult == null || !queryResult.Success || queryResult.Data == null)
                    {
                        throw new BridgeException(503, "READ_ONLY_QUERY_FAILED", "The cardholder-group read-only query did not return a successful result.");
                    }

                    return new
                    {
                        service = "Stamford VMS Genetec Bridge",
                        mode = "read-only",
                        connected = true,
                        sdkVersion = typeof(Engine).Assembly.GetName().Version == null
                            ? "unknown"
                            : typeof(Engine).Assembly.GetName().Version.ToString(),
                        directoryVersion = mainServer.Version == null ? "unknown" : mainServer.Version.ToString(),
                        cardholderGroupsCount = queryResult.Data.Rows.Count,
                        queryStatus = "success",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeOperationsEnabled = IsTestWriteEnabled(),
                        writeScope = IsTestWriteEnabled() ? "one-fixed-test-cardholder-create-only" : "disabled",
                        visitorManagementLicensed = visitorManagementLicensed,
                        visitorSyncEnabled = IsVisitorSyncEnabled() && visitorManagementLicensed,
                        visitorSyncScope = IsVisitorSyncEnabled() && visitorManagementLicensed
                            ? "native-visitor-create-update-for-invited-synthetic-booking-only" : "disabled",
                        visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled() && visitorManagementLicensed
                    };
                }
                catch (BridgeException)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Read-only Genetec operation failed: " + ex.GetType().Name + " — " + ex.Message);
                    throw new BridgeException(503, "GENETEC_READ_ONLY_CHECK_FAILED",
                        "The authenticated read-only query failed. Review the bridge console for the technical exception.");
                }
                finally
                {
                    try { engine.LogOff(); } catch { /* Best-effort session close. */ }
                }
            }
        }

        private static object CreateOrUpdateVmsVisitor(VisitorSyncRequest request)
        {
            if (!IsVisitorSyncEnabled())
            {
                throw new BridgeException(403, "VISITOR_SYNC_DISABLED",
                    "Visitor booking sync is disabled. Restart the bridge and explicitly enable it only for an approved non-production test environment.");
            }

            string bookingId = (request.BookingId ?? String.Empty).Trim().ToUpperInvariant();
            if (!Regex.IsMatch(bookingId, @"^VMS-[A-Z0-9-]{1,24}$", RegexOptions.CultureInvariant))
            {
                throw new BridgeException(400, "INVALID_BOOKING_ID", "Only synthetic VMS booking references such as VMS-2602 can be synchronised.");
            }
            if (!String.Equals((request.Status ?? String.Empty).Trim(), "Invited", StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(409, "VISITOR_STATUS_NOT_ELIGIBLE", "Only an invited synthetic booking can be synchronised by this milestone. Cancelled, checked-in, overdue or checked-out visits are not writable through this endpoint.");
            }

            string firstName = ValidateVisitorField(request.FirstName, "first name", 60);
            string lastName = ValidateVisitorField(request.LastName, "last name", 80);
            DateTime arrivalLocal;
            DateTime departureLocal;
            ParseVisitWindow(request.VisitDate, request.VisitWindow, out arrivalLocal, out departureLocal);
            string operationId = Guid.NewGuid().ToString("D");

            // Stable key makes retries idempotent and does not depend on mutable visitor name or schedule.
            // This is a prototype-owned name namespace, not a replacement for the final approved data-ownership design.
            string entityKey = "STAMFORD VMS BOOKING " + bookingId;
            using (var engine = ConnectToGenetec())
            {
                try
                {
                    var mainServer = engine.GetEntities(EntityType.Server)
                        .OfType<Server>()
                        .FirstOrDefault(server => server.IsMainServer);
                    if (mainServer == null)
                    {
                        throw new BridgeException(503, "DIRECTORY_SERVER_NOT_FOUND", "Authenticated, but the main Directory server entity was not found.");
                    }

                    string directoryVersion = mainServer.Version == null ? "unknown" : mainServer.Version.ToString();
                    if (!directoryVersion.StartsWith("5.11", StringComparison.OrdinalIgnoreCase))
                    {
                        throw new BridgeException(409, "DIRECTORY_VERSION_GUARD_FAILED", "Visitor sync is restricted to a Directory reporting version 5.11. No visitor write was attempted.");
                    }

                    var systemConfiguration = engine.GetEntity(SdkGuids.SystemConfiguration) as SystemConfiguration;
                    bool visitorManagementLicensed = systemConfiguration != null &&
                        systemConfiguration.License != null && systemConfiguration.License.IsVisitorManagementEnabled;
                    if (!visitorManagementLicensed)
                    {
                        throw new BridgeException(409, "VISITOR_MANAGEMENT_NOT_LICENSED",
                            "Security Center does not report Visitor Management as licensed. No visitor write was attempted. Verify licensing and deployment scope with the Genetec administrator.");
                    }

                    // IMPORTANT: Engine.GetEntities(EntityType.Visitor) only enumerates the local
                    // SDK cache. It does not discover server-side Visitors, so using it here caused
                    // each HTTP retry (new Engine instance) to create another Visitor. Always run a
                    // Directory-backed exact-name query before deciding to create.
                    var matches = FindVmsVisitorsByKey(engine, entityKey);
                    if (matches.Count > 1)
                    {
                        throw new BridgeException(409, "DUPLICATE_VMS_VISITOR_ENTITIES",
                            "More than one Genetec Visitor matches this VMS booking key. No update was attempted; reconcile the duplicate entities in Genetec first.");
                    }

                    // Once lifecycle events have been recorded for a booking, do not allow the
                    // invitation upsert endpoint to rewrite its schedule/name or silently recreate
                    // a missing/archived Visitor. Lifecycle history needs explicit reconciliation.
                    LifecycleLedger lifecycleLedger = ReadLifecycleLedger();
                    LifecycleBookingRecord lifecycleBooking = lifecycleLedger.Bookings.FirstOrDefault(item =>
                        String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase));
                    bool lifecycleAlreadyStarted = lifecycleLedger.Events.Any(item =>
                        String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase));
                    if (lifecycleAlreadyStarted)
                    {
                        throw new BridgeException(409, "VISITOR_LIFECYCLE_ALREADY_STARTED",
                            "A lifecycle event already exists for this booking. Invitation upsert is blocked to preserve the event-to-Visitor mapping; use the lifecycle reconciliation procedure instead.");
                    }
                    if (matches.Count == 0 && lifecycleBooking != null)
                    {
                        throw new BridgeException(409, "LIFECYCLE_MAPPED_VISITOR_MISSING",
                            "The lifecycle ledger maps this booking to a Visitor but the active Directory lookup found no Visitor. No new Visitor was created; inspect active/archived records and reconcile the mapping.");
                    }
                    if (matches.Count == 1 && lifecycleBooking != null)
                    {
                        Guid ledgerGuid;
                        if (Guid.TryParse(lifecycleBooking.VisitorGuid, out ledgerGuid) && ledgerGuid != matches[0].Guid)
                            throw new BridgeException(409, "BOOKING_VISITOR_GUID_CONFLICT",
                                "The lifecycle ledger maps this booking to a different GUID than the Directory query. No update was attempted; reconcile manually.");
                    }

                    // For active Visitor entities on the tested 5.11 Directory, Visitor.CheckinDate
                    // is observed to equal the entity Creation date even while Security Desk still offers
                    // the Check in action. Treat Status.ActivationType/ActivationDate as the active-visit
                    // state signal; keep the raw CheckinDate property only as diagnostic evidence.
                    if (matches.Count == 1)
                    {
                        Visitor existingVisitor = engine.GetEntity(matches[0].Guid) as Visitor;
                        if (existingVisitor == null)
                            throw new BridgeException(503, "EXISTING_VISITOR_READBACK_FAILED",
                                "The existing Visitor could not be loaded for a safe pre-registration check. No update was attempted.");
                        DateTime? existingActivationCheckinUtc;
                        string existingLifecycleState = GetActiveVisitorLifecycleState(existingVisitor, out existingActivationCheckinUtc);
                        if (!String.Equals(existingLifecycleState, "pre-registered", StringComparison.OrdinalIgnoreCase))
                            throw new BridgeException(409, existingLifecycleState == "checked-in" ? "VISITOR_ALREADY_CHECKED_IN_UNTRACKED" : "VISITOR_ACTIVATION_STATE_AMBIGUOUS",
                                "The existing Visitor is not in an unambiguous pre-registered state based on Status.ActivationType/ActivationDate. No invitation fields were changed. Inspect Security Desk and reconcile the record before retrying.");
                    }

                    bool created = matches.Count == 0;
                    Visitor visitor = null;

                    // A newly created Visitor can receive default native lifecycle values if CreateEntity
                    // is allowed to commit before its fields are configured. Create and configure it in a
                    // single SDK transaction so the Directory never observes an intermediate default
                    // check-in. The same transaction also keeps updates to an existing invited Visitor
                    // consistent. Synchronous report queries happen only after this transaction commits.
                    engine.TransactionManager.ExecuteTransaction(() =>
                    {
                        visitor = created
                            ? engine.CreateEntity(entityKey, EntityType.Visitor) as Visitor
                            : matches[0];

                        if (visitor == null)
                        {
                            throw new BridgeException(503, "VISITOR_ENTITY_CREATE_FAILED",
                                "Genetec did not return a native Visitor entity. Verify Security Center 5.11 SDK support, Visitor Management licensing and the account's privileges.");
                        }

                        if (visitor.Status == null)
                        {
                            throw new BridgeException(503, "VISITOR_STATUS_UNAVAILABLE",
                                "The SDK did not expose Visitor access status, so the invited Visitor cannot be safely pre-registered for manual check-in.");
                        }

                        SetRequiredStringProperty(visitor, "FirstName", firstName);
                        SetRequiredStringProperty(visitor, "LastName", lastName);
                        visitor.VisitDate = arrivalLocal;

                        // Set Never activation and the intended expiry together, then explicitly write the
                        // Visitor-specific Departure field. Doing a one-argument Activate(MinValue) after
                        // setting Departure resets expiration to Never in this SDK surface.
                        visitor.Status.Activate(DateTime.MinValue, departureLocal);
                        visitor.Departure = departureLocal;
                    });

                    // Force a server-side Visitor report query for this GUID before reading entity fields back.
                    // This distinguishes a successful Directory query from merely trusting the local UI state/cache.
                    if (!VisitorGuidExistsInServerQuery(engine, visitor.Guid))
                    {
                        throw new BridgeException(503, "VISITOR_REPORT_READBACK_FAILED",
                            "The native Visitor write was attempted, but a follow-up Visitor report did not return its GUID. Verify Genetec before retrying.");
                    }

                    Visitor verified = engine.GetEntity(visitor.Guid) as Visitor;
                    if (verified == null || !String.Equals(verified.Name, entityKey, StringComparison.OrdinalIgnoreCase))
                    {
                        throw new BridgeException(503, "VISITOR_READBACK_FAILED",
                            "The visitor write was attempted, but the native Visitor entity could not be read back. Verify Genetec before retrying.");
                    }

                    string verifiedFirstName = GetStringProperty(verified, "FirstName");
                    string verifiedLastName = GetStringProperty(verified, "LastName");
                    if (!String.Equals(verifiedFirstName, firstName, StringComparison.Ordinal) ||
                        !String.Equals(verifiedLastName, lastName, StringComparison.Ordinal))
                    {
                        throw new BridgeException(503, "VISITOR_FIELDS_READBACK_FAILED",
                            "The visitor entity was found, but the first/last-name read-back did not match the submitted VMS booking. Verify Genetec before retrying.");
                    }

                    // Verify the VMS schedule separately from native activation/check-in state. VisitDate
                    // carries the planned arrival; ActivationType must stay Never until the VMS records a
                    // real check-in. This prevents a past scheduled start from masquerading as attendance.
                    DateTime verifiedVisitDate = verified.VisitDate;
                    DateTime verifiedDeparture = verified.Departure;
                    DateTime? verifiedCheckinUtc;
                    string verifiedLifecycleState = GetActiveVisitorLifecycleState(verified, out verifiedCheckinUtc);
                    DateTime? rawEntityCheckinUtc = NormalizeSdkDateToUtc(verified.CheckinDate);
                    if (!SameLocalMinute(verifiedVisitDate, arrivalLocal) ||
                        !SameLocalMinute(verifiedDeparture, departureLocal) ||
                        !String.Equals(verifiedLifecycleState, "pre-registered", StringComparison.OrdinalIgnoreCase) ||
                        verifiedCheckinUtc.HasValue)
                    {
                        throw new BridgeException(503, "VISITOR_PRE_REGISTRATION_READBACK_FAILED",
                            "The native Visitor was not verified as pre-registered. Expected arrival/departure must match and Status must indicate ActivationType=Never with no activation date. The raw active Visitor.CheckinDate property is retained for diagnostics but is not used alone as proof of check-in on this Directory. Inspect Security Desk before retrying.");
                    }

                    PersistBookingVisitorGuid(bookingId, verified.Guid);
                    Console.WriteLine("GUARDED VISITOR SYNC: operation=" + operationId + " " + (created ? "created " : "updated ") + bookingId + " as Genetec Visitor " + verified.Guid.ToString("D") + "; schedule read-back verified and lifecycle mapping stored.");
                    return new
                    {
                        success = true,
                        mode = "guarded-visitor-sync",
                        operation = created ? "create-native-visitor" : "update-native-visitor",
                        created = created,
                        updated = !created,
                        bookingId = bookingId,
                        vmsVisitDate = request.VisitDate,
                        entityType = "Visitor",
                        entityName = verified.Name,
                        visitorGuid = verified.Guid.ToString("D"),
                        firstName = verifiedFirstName,
                        lastName = verifiedLastName,
                        visitDateLocal = ToLocalClockForComparison(verified.VisitDate).ToString("o"),
                        // Backward-compatible field: arrivalLocal is the planned VMS arrival, mapped to VisitDate.
                        // Do not populate it from Visitor.Arrival because that property is native activation.
                        // These *Local fields carry local clock values/offsets, not UTC values suffixed with Z.
                        arrivalLocal = ToLocalClockForComparison(verified.VisitDate).ToString("o"),
                        expectedArrivalLocal = ToLocalClockForComparison(verified.VisitDate).ToString("o"),
                        activationType = verified.Status.ActivationType.ToString(),
                        activationDateLocal = verified.Status.ActivationDate.HasValue ? ToLocalClockForComparison(verified.Status.ActivationDate.Value).ToString("o") : null,
                        genetecCheckinDateUtc = verifiedCheckinUtc.HasValue ? verifiedCheckinUtc.Value.ToString("o") : null,
                        rawEntityCheckinDateUtc = rawEntityCheckinUtc.HasValue ? rawEntityCheckinUtc.Value.ToString("o") : null,
                        activeLifecycleState = verifiedLifecycleState,
                        departureLocal = ToLocalClockForComparison(verified.Departure).ToString("o"),
                        manualCheckInRequired = true,
                        scheduleReadbackVerified = true,
                        nameReadbackVerified = true,
                        serverReportReadbackVerified = true,
                        idempotencyKey = entityKey,
                        directoryVersion = directoryVersion,
                        visitorManagementLicensed = visitorManagementLicensed,
                        credentialCreated = false,
                        accessRightsChanged = false,
                        escortAssigned = false,
                        emailSent = false,
                        hostAndDestinationMappedToGenetec = false,
                        message = created
                            ? "The native Genetec Visitor was created and verified by read-back. No credential, access rights or escort were assigned."
                            : "The existing native Genetec Visitor was updated and verified by read-back. No credential, access rights or escort were changed.",
                        operationId = operationId,
                        completedAtUtc = DateTime.UtcNow.ToString("o"),
                        visitorSyncEnabled = true
                    };
                }
                catch (BridgeException ex)
                {
                    Console.Error.WriteLine("Native visitor sync rejected/failed: operation=" + operationId + " booking=" + bookingId + " code=" + ex.Code);
                    throw;
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Native visitor sync failed: operation=" + operationId + " booking=" + bookingId + " " + ex.GetType().Name + " — " + ex.Message);
                    throw new BridgeException(503, "NATIVE_VISITOR_SYNC_FAILED",
                        "Genetec rejected or could not complete this native Visitor operation. Review local bridge logs and confirm the 5.11 SDK, Visitor Management licence and account privileges before retrying.");
                }
                finally
                {
                    try { engine.LogOff(); } catch { /* Best-effort session close. */ }
                }
            }
        }

        private static object ApplyVisitorLifecycleEvent(VisitorLifecycleRequest request)
        {
            if (!IsVisitorLifecycleSyncEnabled())
            {
                throw new BridgeException(403, "VISITOR_LIFECYCLE_DISABLED",
                    "Native Visitor lifecycle sync is disabled. Restart the bridge and explicitly enable it only for an approved non-production test environment.");
            }
            if (!HasLifecycleSdkMembers())
            {
                throw new BridgeException(409, "VISITOR_LIFECYCLE_SDK_MAPPING_UNAVAILABLE",
                    "The loaded SDK does not expose the required Visitor activation, check-in read-back and checkout members. No lifecycle write was attempted.");
            }

            string bookingId = (request.BookingId ?? String.Empty).Trim().ToUpperInvariant();
            string eventId = (request.EventId ?? String.Empty).Trim().ToUpperInvariant();
            string eventType = (request.EventType ?? String.Empty).Trim();
            string vmsStatus = (request.VmsStatus ?? String.Empty).Trim();
            if (!IsAllowedSyntheticLifecycleBookingId(bookingId))
                throw new BridgeException(400, "NON_SYNTHETIC_BOOKING_REJECTED", "Lifecycle writes accept only VMS-SDKTEST-* test harness IDs or prototype-generated VMS-UI-{six hex digits} IDs.");
            if (!IsAllowedSyntheticLifecycleEventId(bookingId, eventId))
                throw new BridgeException(400, "INVALID_EVENT_ID", "EventId must begin with the exact synthetic BookingId followed by a stable alphanumeric event suffix.");
            if (!String.Equals(eventType, "CheckIn", StringComparison.Ordinal) && !String.Equals(eventType, "CheckOut", StringComparison.Ordinal))
                throw new BridgeException(400, "INVALID_EVENT_TYPE", "EventType must be exactly CheckIn or CheckOut.");
            if ((eventType == "CheckIn" && vmsStatus != "Inside") || (eventType == "CheckOut" && vmsStatus != "Checked out"))
                throw new BridgeException(400, "LIFECYCLE_STATUS_MISMATCH", "CheckIn requires VmsStatus=Inside and CheckOut requires VmsStatus=Checked out.");

            DateTimeOffset eventTimeOffset;
            if (String.IsNullOrWhiteSpace(request.EventAtUtc) ||
                !request.EventAtUtc.Trim().EndsWith("Z", StringComparison.OrdinalIgnoreCase) ||
                !DateTimeOffset.TryParse(request.EventAtUtc, CultureInfo.InvariantCulture, DateTimeStyles.AssumeUniversal | DateTimeStyles.AdjustToUniversal, out eventTimeOffset))
            {
                throw new BridgeException(400, "INVALID_EVENT_TIME", "EventAtUtc must be a valid ISO-8601 UTC timestamp ending in Z.");
            }
            DateTime eventAtUtc = eventTimeOffset.UtcDateTime;

            // Validate replay and ordering before contacting Genetec. A durable local ledger makes duplicate
            // requests safe across bridge restarts; actual state is still read back from the SDK after mutations.
            LifecycleLedger ledger = ReadLifecycleLedger();
            LifecycleEventRecord priorEvent = ledger.Events.FirstOrDefault(item =>
                String.Equals(item.EventId, eventId, StringComparison.OrdinalIgnoreCase));
            if (priorEvent != null)
            {
                bool samePayload = String.Equals(priorEvent.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                    String.Equals(priorEvent.EventType, eventType, StringComparison.Ordinal) &&
                    String.Equals(priorEvent.VmsStatus, vmsStatus, StringComparison.Ordinal) &&
                    String.Equals(priorEvent.EventAtUtc, eventAtUtc.ToString("o"), StringComparison.OrdinalIgnoreCase);
                if (!samePayload)
                    throw new BridgeException(409, "EVENT_ID_PAYLOAD_CONFLICT", "This EventId was already used with a different booking, event type, timestamp or VMS status.");
                return LifecycleResponse(priorEvent, true);
            }

            // Enforce freshness only for a new EventId. Exact replays are resolved from the ledger above,
            // even when the original event timestamp is now older than the new-event window.
            DateTime nowUtc = DateTime.UtcNow;
            if (eventAtUtc < nowUtc.AddMinutes(-30) || eventAtUtc > nowUtc.AddMinutes(2))
                throw new BridgeException(400, "EVENT_TIME_OUT_OF_RANGE", "A new synthetic lifecycle event must be within the last 30 minutes and no more than 2 minutes in the future.");

            LifecycleEventRecord priorCheckIn = ledger.Events
                .Where(item => String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                    String.Equals(item.EventType, "CheckIn", StringComparison.Ordinal))
                .OrderBy(item => item.EventAtUtc, StringComparer.Ordinal)
                .LastOrDefault();
            LifecycleEventRecord priorCheckOut = ledger.Events.FirstOrDefault(item =>
                String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                String.Equals(item.EventType, "CheckOut", StringComparison.Ordinal));

            if (eventType == "CheckIn" && (priorCheckIn != null || priorCheckOut != null))
                throw new BridgeException(409, "VISITOR_ALREADY_CHECKED_IN_OR_CLOSED", "This synthetic booking already has a lifecycle check-in or checkout event. Use the original EventId for retries; do not start a second visit on the same booking.");
            if (eventType == "CheckOut" && priorCheckIn == null)
                throw new BridgeException(409, "CHECKOUT_BEFORE_VERIFIED_CHECKIN", "Checkout is rejected because the VMS bridge ledger has no successfully read-back-verified check-in event for this booking.");
            if (eventType == "CheckOut" && priorCheckOut != null)
                throw new BridgeException(409, "VISITOR_ALREADY_CHECKED_OUT", "A checkout has already been recorded for this booking. Retry with the original EventId to retrieve its result.");
            if (eventType == "CheckOut")
            {
                DateTime checkinTime;
                if (!DateTime.TryParse(priorCheckIn.EventAtUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out checkinTime) || eventAtUtc <= checkinTime.ToUniversalTime())
                    throw new BridgeException(409, "LIFECYCLE_EVENT_OUT_OF_ORDER", "Checkout event time must be later than the verified check-in event time.");
            }

            Guid visitorGuid = Guid.Empty;
            LifecycleBookingRecord bookingRecord = ledger.Bookings.FirstOrDefault(item =>
                String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase));
            if (bookingRecord != null) Guid.TryParse(bookingRecord.VisitorGuid, out visitorGuid);

            string entityKey = "STAMFORD VMS BOOKING " + bookingId;
            string operationId = Guid.NewGuid().ToString("D");
            DateTime? checkinUtc = null;
            DateTime? checkoutUtc = null;
            string visitorName = entityKey;
            string directoryVersion;
            string lifecycleStage = "open-sdk-session";

            using (var engine = ConnectToGenetec())
            {
                try
                {
                    lifecycleStage = "verify-directory-and-license";
                    directoryVersion = RequireVisitorManagementDirectory(engine);

                    // Always verify the live Directory mapping, even when the local ledger has a GUID.
                    // This catches duplicates or a stale/corrupt mapping before a native lifecycle mutation.
                    List<Visitor> directoryMatches = FindVmsVisitorsByKey(engine, entityKey);
                    if (directoryMatches.Count == 0)
                        throw new BridgeException(404, "VISITOR_BOOKING_NOT_FOUND", "No active native Visitor matches this synthetic booking key. Sync an invited booking first; no lifecycle write was attempted.");
                    if (directoryMatches.Count > 1)
                        throw new BridgeException(409, "DUPLICATE_VMS_VISITOR_ENTITIES", "More than one active native Visitor matches this booking key. Reconcile duplicates before lifecycle writes.");

                    Guid directoryVisitorGuid = directoryMatches[0].Guid;
                    if (visitorGuid != Guid.Empty && visitorGuid != directoryVisitorGuid)
                        throw new BridgeException(409, "BOOKING_VISITOR_GUID_CONFLICT", "The durable booking mapping does not match the unique Directory result. Reconcile the mapping manually; no lifecycle mutation was sent.");
                    visitorGuid = directoryVisitorGuid;
                    PersistBookingVisitorGuid(bookingId, visitorGuid);

                    lifecycleStage = "load-active-visitor";
                    Visitor visitor = engine.GetEntity(visitorGuid) as Visitor;
                    if (visitor == null || visitor.IsDisposed)
                    {
                        // A checked-out Visitor is no longer an active Visitor entity. The ledger prevents
                        // repeated checkout writes; do not recreate a visit as a side effect of a lifecycle call.
                        throw new BridgeException(409, "ACTIVE_VISITOR_NOT_FOUND", "The mapped native Visitor is not active. Inspect archived Visitor records before retrying; no new Visitor was created.");
                    }
                    if (!String.Equals(visitor.Name, entityKey, StringComparison.OrdinalIgnoreCase))
                        throw new BridgeException(409, "VISITOR_BOOKING_KEY_MISMATCH", "The mapped Visitor name does not match the stable VMS booking key.");
                    visitorName = visitor.Name;
                    if (visitor.Credentials != null && visitor.Credentials.Any())
                        throw new BridgeException(409, "VISITOR_HAS_CREDENTIALS", "Lifecycle test is restricted to synthetic Visitors without credentials, so no credential state can be changed.");

                    DateTime? existingCheckinUtc;
                    string activeLifecycleState = GetActiveVisitorLifecycleState(visitor, out existingCheckinUtc);
                    if (eventType == "CheckIn")
                    {
                        lifecycleStage = "check-in-activation-mutation";
                        if (String.Equals(activeLifecycleState, "checked-in", StringComparison.OrdinalIgnoreCase))
                            throw new BridgeException(409, "VISITOR_ALREADY_CHECKED_IN_UNTRACKED", "Genetec reports an activation/check-in based on Status.ActivationDate, but no matching bridge event exists. Reconcile the existing visit before sending another check-in.");
                        if (!String.Equals(activeLifecycleState, "pre-registered", StringComparison.OrdinalIgnoreCase))
                            throw new BridgeException(409, "VISITOR_ACTIVATION_STATE_AMBIGUOUS", "The Visitor activation state is not a confirmed pre-registered state. No lifecycle mutation was attempted.");
                        if (!HasSdkPrivilege(engine, "CheckInVisitors"))
                            throw new BridgeException(403, "CHECKIN_PRIVILEGE_DENIED", "The connected SDK identity is not confirmed to have the Genetec CheckInVisitors privilege. No lifecycle mutation was attempted.");

                        // Genetec documents the activation date as the visitor's check-in date. Use the
                        // modern Status.Activate API (ActivationMode is obsolete), then set Visitor.Arrival
                        // to the same actual event time. Verify a fresh SDK session's Status state, not the
                        // active entity CheckinDate property, which has matched Creation date in this Directory.
                        DateTime localEventTime = eventAtUtc.ToLocalTime();
                        if (visitor.Status == null)
                            throw new BridgeException(503, "VISITOR_STATUS_UNAVAILABLE", "The Visitor Status object is unavailable; no check-in mutation was attempted.");
                        visitor.Status.Activate(localEventTime);
                        visitor.Arrival = localEventTime;
                        lifecycleStage = "check-in-independent-readback";
                        checkinUtc = ReadActiveVisitorCheckinUtc(visitorGuid);
                        if (!checkinUtc.HasValue)
                            throw new BridgeException(503, "CHECKIN_READBACK_FAILED", "Activation was submitted, but a fresh SDK session did not verify Status.ActivationType=SpecificDate and a matching ActivationDate. Inspect Security Center; no success is reported.");
                        if (Math.Abs((checkinUtc.Value - eventAtUtc).TotalMinutes) > 10)
                            throw new BridgeException(503, "CHECKIN_TIMESTAMP_READBACK_MISMATCH", "Genetec reported a check-in timestamp materially different from the requested event time. Inspect Security Center before retrying.");
                    }
                    else
                    {
                        if (!existingCheckinUtc.HasValue)
                            throw new BridgeException(409, "GENETEC_CHECKIN_NOT_CONFIRMED", "Genetec does not report a native check-in for this Visitor. Checkout was not attempted.");
                        if (priorCheckIn == null || !priorCheckIn.ReadBackVerified)
                            throw new BridgeException(409, "CHECKOUT_CHECKIN_LEDGER_UNVERIFIED", "Checkout requires a previously read-back-verified check-in event. No checkout mutation was attempted.");
                        Guid verifiedCheckInGuid;
                        DateTime verifiedLedgerCheckinUtc;
                        if (!Guid.TryParse(priorCheckIn.VisitorGuid, out verifiedCheckInGuid) || verifiedCheckInGuid != visitorGuid ||
                            !DateTime.TryParse(priorCheckIn.GenetecCheckinDateUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out verifiedLedgerCheckinUtc))
                            throw new BridgeException(409, "CHECKOUT_CHECKIN_LEDGER_INVALID", "The prior check-in ledger does not contain the mapped Visitor GUID and a valid independently verified native check-in timestamp. No checkout mutation was attempted.");
                        if (Math.Abs((existingCheckinUtc.Value - verifiedLedgerCheckinUtc.ToUniversalTime()).TotalSeconds) > 2)
                            throw new BridgeException(409, "CHECKOUT_CHECKIN_READBACK_MISMATCH", "The active Visitor's native activation/check-in timestamp does not match the previously verified check-in ledger. No checkout mutation was attempted.");
                        if (!HasSdkPrivilege(engine, "CheckOutVisitors"))
                            throw new BridgeException(403, "CHECKOUT_PRIVILEGE_DENIED", "The connected SDK identity is not confirmed to have the Genetec CheckOutVisitors privilege. No lifecycle mutation was attempted.");

                        // Preserve the canonical check-in time verified from Visitor.Status.ActivationDate and
                        // cross-checked against the saved CheckIn event. On this Directory, the archive's raw
                        // CheckinDate can reflect entity creation time rather than the actual activation-based
                        // check-in. The archived CheckinDate is diagnostic only; never overwrite canonical
                        // check-in evidence with it.
                        DateTime canonicalCheckinUtc = existingCheckinUtc.Value;

                        // Checkout can remove the Visitor from the active list before the archive is queryable.
                        // First log the mutation stage, then poll the read-only archived lookup for a bounded
                        // interval. Only the documented InvalidValue (archive not visible yet) is retried; all
                        // other SDK errors are surfaced with a safe stage/code diagnostic.
                        lifecycleStage = "invoke-visitor-checkout";
                        visitor.Checkout();
                        lifecycleStage = "poll-archived-visitor-readback";
                        string archiveLookupDiagnostic;
                        ArchivedVisitor archived = ReadArchivedVisitorWithRetry(visitorGuid, 15000, out archiveLookupDiagnostic);
                        if (archived == null)
                            throw new BridgeException(503, "CHECKOUT_ARCHIVE_NOT_VISIBLE",
                                "Visitor.Checkout() returned, but the Directory did not expose a verified archived CheckedOut record within 15 seconds. Last archive lookup: " + archiveLookupDiagnostic + ". The mutation may have succeeded; inspect GET /api/genetec/visitor-lifecycle-state before retrying.");
                        lifecycleStage = "validate-archived-visitor-readback";
                        if (archived.VisitState != VisitStates.CheckedOut || archived.CheckoutDate == DateTime.MinValue)
                            throw new BridgeException(503, "CHECKOUT_READBACK_FAILED", "The archived Visitor was found, but its state or native checkout timestamp is incomplete. Inspect GET /api/genetec/visitor-lifecycle-state before retrying.");
                        checkinUtc = canonicalCheckinUtc;
                        checkoutUtc = NormalizeSdkDateToUtc(archived.CheckoutDate);
                        if (!checkoutUtc.HasValue || Math.Abs((checkoutUtc.Value - eventAtUtc).TotalMinutes) > 10)
                            throw new BridgeException(503, "CHECKOUT_TIMESTAMP_READBACK_MISMATCH", "Genetec archived the visitor, but the reported checkout timestamp materially differs from the submitted event. Inspect Security Center before retrying.");
                    }
                }
                catch (BridgeException) { throw; }
                catch (Exception ex)
                {
                    SdkException sdkException = ex as SdkException;
                    string sdkErrorCode = sdkException == null ? "n/a" : sdkException.ErrorCode.ToString();
                    Console.Error.WriteLine("Visitor lifecycle SDK operation failed: operation=" + operationId +
                        " booking=" + bookingId + " event=" + eventType + " stage=" + lifecycleStage +
                        " exception=" + ex.GetType().FullName + " sdkError=" + sdkErrorCode + " — " + ex.Message);
                    string errorCode = eventType == "CheckIn"
                        ? "CHECKIN_SDK_EXCEPTION"
                        : (String.Equals(lifecycleStage, "invoke-visitor-checkout", StringComparison.Ordinal)
                            ? "CHECKOUT_MUTATION_SDK_EXCEPTION" : "CHECKOUT_READBACK_SDK_EXCEPTION");
                    throw new BridgeException(503, errorCode,
                        "Genetec raised " + ex.GetType().Name + " at stage '" + lifecycleStage + "' (SDK error: " + sdkErrorCode + "). The operation may have reached Genetec. Inspect GET /api/genetec/visitor-lifecycle-state and the bridge log before retrying.");
                }
                finally
                {
                    try { engine.LogOff(); } catch { /* Best-effort session close. */ }
                }
            }

            if (eventType == "CheckIn" && checkinUtc.HasValue)
            {
                MutateLifecycleLedger(store =>
                {
                    AddOrUpdateBookingMapping(store, bookingId, visitorGuid);
                    store.Events.Add(new LifecycleEventRecord
                    {
                        BookingId = bookingId,
                        EventId = eventId,
                        EventType = eventType,
                        EventAtUtc = eventAtUtc.ToString("o"),
                        VmsStatus = vmsStatus,
                        VisitorGuid = visitorGuid.ToString("D"),
                        OperationId = operationId,
                        GenetecCheckinDateUtc = checkinUtc.Value.ToString("o"),
                        GenetecCheckoutDateUtc = null,
                        ReadBackVerified = true,
                        CompletedAtUtc = DateTime.UtcNow.ToString("o")
                    });
                });
            }
            else if (eventType == "CheckOut" && checkinUtc.HasValue && checkoutUtc.HasValue)
            {
                MutateLifecycleLedger(store =>
                {
                    AddOrUpdateBookingMapping(store, bookingId, visitorGuid);
                    store.Events.Add(new LifecycleEventRecord
                    {
                        BookingId = bookingId,
                        EventId = eventId,
                        EventType = eventType,
                        EventAtUtc = eventAtUtc.ToString("o"),
                        VmsStatus = vmsStatus,
                        VisitorGuid = visitorGuid.ToString("D"),
                        OperationId = operationId,
                        GenetecCheckinDateUtc = checkinUtc.Value.ToString("o"),
                        GenetecCheckoutDateUtc = checkoutUtc.Value.ToString("o"),
                        ReadBackVerified = true,
                        CompletedAtUtc = DateTime.UtcNow.ToString("o")
                    });
                    if (store.Events.Any(item => String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                        String.Equals(item.EventType, "CheckIn", StringComparison.Ordinal)) &&
                        store.Events.Any(item => String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                        String.Equals(item.EventType, "CheckOut", StringComparison.Ordinal)))
                        store.LifecycleOperationMappingVerified = true;
                });
            }

            LifecycleEventRecord saved = ReadLifecycleLedger().Events.First(item => String.Equals(item.EventId, eventId, StringComparison.OrdinalIgnoreCase));
            Console.WriteLine("GUARDED VISITOR LIFECYCLE: operation=" + operationId + " booking=" + bookingId + " event=" + eventType + " visitor=" + visitorGuid.ToString("D") + " read-back verified.");
            return LifecycleResponse(saved, false, visitorName, directoryVersion);
        }

        private static bool IsAllowedSyntheticLifecycleBookingId(string bookingId)
        {
            if (String.IsNullOrWhiteSpace(bookingId)) return false;
            string value = bookingId.Trim().ToUpperInvariant();
            // SDK harness IDs support richer suffixes; UI IDs are exactly six hex digits
            // generated by the prototype. Arbitrary VMS-* or real booking IDs stay blocked.
            return Regex.IsMatch(value, @"^VMS-SDKTEST-[A-Z0-9-]{1,16}$", RegexOptions.CultureInvariant) ||
                   Regex.IsMatch(value, @"^VMS-UI-[0-9A-F]{6}$", RegexOptions.CultureInvariant);
        }

        private static bool IsAllowedSyntheticLifecycleEventId(string bookingId, string eventId)
        {
            if (String.IsNullOrWhiteSpace(bookingId) || String.IsNullOrWhiteSpace(eventId)) return false;
            string expectedPrefix = bookingId.Trim().ToUpperInvariant() + "-";
            string candidate = eventId.Trim().ToUpperInvariant();
            if (!candidate.StartsWith(expectedPrefix, StringComparison.Ordinal)) return false;
            string suffix = candidate.Substring(expectedPrefix.Length);
            return Regex.IsMatch(suffix, @"^[A-Z0-9-]{1,64}$", RegexOptions.CultureInvariant);
        }

        private static object ReconcileVerifiedArchivedCheckoutToLedger(string rawBookingId)
        {
            if (!IsVisitorLifecycleSyncEnabled())
                throw new BridgeException(403, "VISITOR_LIFECYCLE_DISABLED",
                    "Lifecycle ledger reconciliation is disabled. Restart the bridge with the explicit synthetic lifecycle opt-in in the approved non-production environment.");

            string bookingId = (rawBookingId ?? String.Empty).Trim().ToUpperInvariant();
            if (!Regex.IsMatch(bookingId, @"^VMS-SDKTEST-[A-Z0-9-]{1,16}$", RegexOptions.CultureInvariant))
                throw new BridgeException(400, "NON_SYNTHETIC_BOOKING_REJECTED", "Ledger reconciliation is restricted to VMS-SDKTEST-* booking IDs.");

            LifecycleLedger initialLedger = ReadLifecycleLedger();
            LifecycleBookingRecord initialMapping = initialLedger.Bookings.FirstOrDefault(item =>
                String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase));
            Guid visitorGuid;
            if (initialMapping == null || !Guid.TryParse(initialMapping.VisitorGuid, out visitorGuid) || visitorGuid == Guid.Empty)
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_MAPPING_MISSING", "The ledger has no stable Visitor GUID for this booking. No reconciliation was made.");

            List<LifecycleEventRecord> bookingEvents = initialLedger.Events.Where(item =>
                String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase)).ToList();
            List<LifecycleEventRecord> checkIns = bookingEvents.Where(item =>
                String.Equals(item.EventType, "CheckIn", StringComparison.Ordinal) && item.ReadBackVerified).ToList();
            if (checkIns.Count != 1)
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_CHECKIN_NOT_UNIQUE", "Exactly one previously verified check-in event is required before reconciling a checkout. No reconciliation was made.");

            LifecycleEventRecord priorCheckIn = checkIns[0];
            Guid checkInGuid;
            if (!Guid.TryParse(priorCheckIn.VisitorGuid, out checkInGuid) || checkInGuid != visitorGuid)
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_GUID_CONFLICT", "The verified check-in event GUID does not match the booking mapping. No reconciliation was made.");

            List<LifecycleEventRecord> existingCheckOuts = bookingEvents.Where(item =>
                String.Equals(item.EventType, "CheckOut", StringComparison.Ordinal)).ToList();
            if (existingCheckOuts.Count > 1)
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_LEDGER_DUPLICATE", "The local ledger already contains multiple checkout events. Reconcile it manually; no change was made.");

            string entityKey = "STAMFORD VMS BOOKING " + bookingId;
            string directoryVersion;
            using (var engine = ConnectToGenetec())
            {
                try
                {
                    directoryVersion = RequireVisitorManagementDirectory(engine);
                    List<Visitor> activeMatches = FindVmsVisitorsByKey(engine, entityKey);
                    if (activeMatches.Count > 1)
                        throw new BridgeException(409, "DUPLICATE_VMS_VISITOR_ENTITIES", "Multiple active Visitors match the booking key. No ledger reconciliation was made.");
                    if (activeMatches.Count != 0)
                        throw new BridgeException(409, "VISITOR_STILL_ACTIVE", "An active Visitor still matches this booking. Checkout reconciliation requires no active match and one confirmed archived CheckedOut visit. No reconciliation was made.");
                }
                finally { try { engine.LogOff(); } catch { } }
            }

            ArchivedVisitor archived;
            try { archived = ReadArchivedVisitor(visitorGuid); }
            catch (SdkException ex)
            {
                throw new BridgeException(503, "CHECKOUT_RECONCILIATION_ARCHIVE_QUERY_FAILED",
                    "The archived Visitor query failed with SDK error " + ex.ErrorCode + ". No ledger reconciliation was made.");
            }
            if (archived == null)
                throw new BridgeException(404, "CHECKOUT_RECONCILIATION_ARCHIVE_NOT_FOUND", "No archived visit was found for the original Visitor GUID. No ledger reconciliation was made.");
            if (archived.VisitState != VisitStates.CheckedOut)
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_NOT_CHECKED_OUT", "The archived visit is not in native CheckedOut state. No ledger reconciliation was made.");

            DateTime? archivedCheckinUtc = NormalizeSdkDateToUtc(archived.CheckinDate);
            DateTime? archivedCheckoutUtc = NormalizeSdkDateToUtc(archived.CheckoutDate);
            if (!archivedCheckoutUtc.HasValue)
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_CHECKOUT_TIME_MISSING", "The archived visit has no valid native checkout timestamp. No ledger reconciliation was made.");

            DateTime priorEventTime;
            if (!DateTime.TryParse(priorCheckIn.EventAtUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out priorEventTime))
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_CHECKIN_TIME_INVALID", "The saved check-in event timestamp is invalid. No reconciliation was made.");
            if (archivedCheckoutUtc.Value <= priorEventTime.ToUniversalTime())
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_EVENT_ORDER_INVALID", "The archived checkout timestamp is not later than the previously verified check-in event. No reconciliation was made.");

            DateTime? canonicalCheckinUtc = null;
            DateTime parsedCanonicalCheckin;
            if (DateTime.TryParse(priorCheckIn.GenetecCheckinDateUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out parsedCanonicalCheckin))
                canonicalCheckinUtc = parsedCanonicalCheckin.ToUniversalTime();
            if (!canonicalCheckinUtc.HasValue)
                throw new BridgeException(409, "CHECKOUT_RECONCILIATION_CHECKIN_READBACK_MISSING", "The saved check-in event lacks its verified native check-in timestamp. No reconciliation was made.");

            string eventId = bookingId + "-" + archivedCheckoutUtc.Value.ToString("yyyyMMddTHHmmssfffZ", CultureInfo.InvariantCulture) + "-COR";
            string operationId = Guid.NewGuid().ToString("D");
            bool alreadyReconciled = false;
            var newCheckout = new LifecycleEventRecord
            {
                BookingId = bookingId,
                EventId = eventId,
                EventType = "CheckOut",
                EventAtUtc = archivedCheckoutUtc.Value.ToString("o", CultureInfo.InvariantCulture),
                VmsStatus = "Checked out",
                VisitorGuid = visitorGuid.ToString("D"),
                OperationId = operationId,
                GenetecCheckinDateUtc = canonicalCheckinUtc.Value.ToString("o", CultureInfo.InvariantCulture),
                GenetecCheckoutDateUtc = archivedCheckoutUtc.Value.ToString("o", CultureInfo.InvariantCulture),
                ReadBackVerified = true,
                CompletedAtUtc = DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture),
                ReconciledFromArchive = true,
                ReconciliationNote = "Local ledger reconciled from a read-only Directory/archive check confirming native VisitStates.CheckedOut; no Genetec mutation was issued by this reconciliation."
            };

            MutateLifecycleLedger(store =>
            {
                List<LifecycleEventRecord> currentCheckouts = store.Events.Where(item =>
                    String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                    String.Equals(item.EventType, "CheckOut", StringComparison.Ordinal)).ToList();
                if (currentCheckouts.Count > 1)
                    throw new BridgeException(409, "CHECKOUT_RECONCILIATION_LEDGER_DUPLICATE", "Multiple checkout events appeared in the ledger during reconciliation. No change was made.");
                if (currentCheckouts.Count == 1)
                {
                    LifecycleEventRecord existing = currentCheckouts[0];
                    Guid existingGuid;
                    DateTime existingCheckout;
                    DateTime existingCanonicalCheckin;
                    bool existingCheckinMatchesCanonical = DateTime.TryParse(existing.GenetecCheckinDateUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out existingCanonicalCheckin) &&
                        Math.Abs((existingCanonicalCheckin.ToUniversalTime() - canonicalCheckinUtc.Value).TotalSeconds) <= 2;
                    if (existing.ReadBackVerified && Guid.TryParse(existing.VisitorGuid, out existingGuid) && existingGuid == visitorGuid &&
                        DateTime.TryParse(existing.GenetecCheckoutDateUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out existingCheckout) &&
                        Math.Abs((existingCheckout.ToUniversalTime() - archivedCheckoutUtc.Value).TotalSeconds) <= 2 && existingCheckinMatchesCanonical)
                    {
                        alreadyReconciled = true;
                        store.LifecycleOperationMappingVerified = true;
                        return;
                    }
                    throw new BridgeException(409, "CHECKOUT_RECONCILIATION_CONFLICT", "An existing checkout ledger event conflicts with the verified archived checkout or the prior canonical check-in timestamp. No change was made.");
                }

                LifecycleEventRecord currentCheckIn = store.Events.FirstOrDefault(item =>
                    String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                    String.Equals(item.EventType, "CheckIn", StringComparison.Ordinal) && item.ReadBackVerified);
                Guid currentCheckInGuid;
                if (currentCheckIn == null || !Guid.TryParse(currentCheckIn.VisitorGuid, out currentCheckInGuid) || currentCheckInGuid != visitorGuid)
                    throw new BridgeException(409, "CHECKOUT_RECONCILIATION_CHECKIN_CHANGED", "The verified check-in ledger changed or no longer matches the archive. No change was made.");

                LifecycleEventRecord eventIdCollision = store.Events.FirstOrDefault(item => String.Equals(item.EventId, eventId, StringComparison.OrdinalIgnoreCase));
                if (eventIdCollision != null)
                    throw new BridgeException(409, "CHECKOUT_RECONCILIATION_EVENT_ID_CONFLICT", "The deterministic reconciliation EventId is already used by a different ledger event. No change was made.");

                AddOrUpdateBookingMapping(store, bookingId, visitorGuid);
                store.Events.Add(newCheckout);
                store.LifecycleOperationMappingVerified = true;
            });

            LifecycleEventRecord saved = ReadLifecycleLedger().Events.First(item =>
                String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase) &&
                String.Equals(item.EventType, "CheckOut", StringComparison.Ordinal));
            Console.WriteLine("LOCAL LEDGER RECONCILIATION: booking=" + bookingId + " visitor=" + visitorGuid.ToString("D") +
                " state=CheckedOut event=" + saved.EventId + " alreadyReconciled=" + alreadyReconciled + "; no Genetec mutation was issued.");

            return new
            {
                success = saved.ReadBackVerified,
                mode = "reconcile-verified-archived-checkout-to-local-ledger",
                operation = "reconcile-archived-checkout",
                bookingId = bookingId,
                entityKey = entityKey,
                visitorGuid = visitorGuid.ToString("D"),
                archivedVisitGuid = archived.Guid.ToString("D"),
                visitState = archived.VisitState.ToString(),
                eventId = saved.EventId,
                eventType = saved.EventType,
                eventAtUtc = saved.EventAtUtc,
                genetecCheckinDateUtc = saved.GenetecCheckinDateUtc,
                genetecCheckoutDateUtc = saved.GenetecCheckoutDateUtc,
                archivedCheckinDateUtcDiagnostic = archivedCheckinUtc?.ToString("o", CultureInfo.InvariantCulture),
                readBackVerified = saved.ReadBackVerified,
                reconciledFromArchive = saved.ReconciledFromArchive,
                alreadyReconciled = alreadyReconciled,
                ledgerMutationAttempted = true,
                genetecWriteAttempted = false,
                directoryVersion = directoryVersion,
                nextAction = "Run the read-only lifecycle state inspection and verify the saved checkout event ID; do not repeat the Genetec checkout."
            };
        }

        private static object LifecycleResponse(LifecycleEventRecord item, bool alreadyApplied, string visitorName = null, string directoryVersion = null)
        {
            return new
            {
                success = item.ReadBackVerified,
                mode = "guarded-visitor-lifecycle",
                operation = item.EventType == "CheckIn" ? "native-visitor-checkin" : "native-visitor-checkout",
                bookingId = item.BookingId,
                eventId = item.EventId,
                eventType = item.EventType,
                vmsStatus = item.VmsStatus,
                visitorGuid = item.VisitorGuid,
                visitorName = visitorName,
                operationId = item.OperationId,
                eventAtUtc = item.EventAtUtc,
                genetecCheckinDateUtc = item.GenetecCheckinDateUtc,
                genetecCheckoutDateUtc = item.GenetecCheckoutDateUtc,
                readBackVerified = item.ReadBackVerified,
                alreadyApplied = alreadyApplied,
                directoryVersion = directoryVersion,
                visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled(),
                lifecycleOperationMappingVerified = IsLifecycleOperationMappingVerified(),
                credentialChanged = false,
                accessRightsChanged = false,
                completedAtUtc = item.CompletedAtUtc
            };
        }

        private static string RequireVisitorManagementDirectory(Engine engine)
        {
            var mainServer = engine.GetEntities(EntityType.Server).OfType<Server>().FirstOrDefault(server => server.IsMainServer);
            if (mainServer == null) throw new BridgeException(503, "DIRECTORY_SERVER_NOT_FOUND", "The main Directory server entity was not found.");
            string directoryVersion = mainServer.Version == null ? "unknown" : mainServer.Version.ToString();
            if (!directoryVersion.StartsWith("5.11", StringComparison.OrdinalIgnoreCase))
                throw new BridgeException(409, "DIRECTORY_VERSION_GUARD_FAILED", "Native Visitor lifecycle writes are restricted to Security Center 5.11.");
            var systemConfiguration = engine.GetEntity(SdkGuids.SystemConfiguration) as SystemConfiguration;
            bool licensed = systemConfiguration != null && systemConfiguration.License != null && systemConfiguration.License.IsVisitorManagementEnabled;
            if (!licensed) throw new BridgeException(409, "VISITOR_MANAGEMENT_NOT_LICENSED", "Visitor Management is not reported as licensed. No lifecycle mutation was attempted.");
            return directoryVersion;
        }

        private static bool HasLifecycleSdkMembers()
        {
            PropertyInfo arrival = typeof(Visitor).GetProperty("Arrival", BindingFlags.Public | BindingFlags.Instance);
            PropertyInfo status = typeof(Visitor).GetProperty("Status", BindingFlags.Public | BindingFlags.Instance);
            PropertyInfo checkinDate = typeof(Visitor).GetProperty("CheckinDate", BindingFlags.Public | BindingFlags.Instance);
            MethodInfo checkout = typeof(Visitor).GetMethod("Checkout", BindingFlags.Public | BindingFlags.Instance, null, Type.EmptyTypes, null);
            MethodInfo activate = status == null ? null : status.PropertyType.GetMethod("Activate", BindingFlags.Public | BindingFlags.Instance, null, new[] { typeof(DateTime) }, null);
            return arrival != null && arrival.CanWrite && status != null && activate != null &&
                checkinDate != null && checkinDate.CanRead && checkout != null;
        }

        private static bool HasSdkPrivilege(Engine engine, string fieldName)
        {
            try
            {
                FieldInfo field = typeof(SdkPrivilege).GetField(fieldName, BindingFlags.Public | BindingFlags.Static);
                if (field == null) return false;
                object privilege = field.GetValue(null);
                if (privilege == null) return false;
                Type privilegeType = privilege.GetType();
                object guidValue = null;
                PropertyInfo guidProperty = privilegeType.GetProperty("Guid", BindingFlags.Public | BindingFlags.Instance) ??
                    privilegeType.GetProperty("Id", BindingFlags.Public | BindingFlags.Instance);
                if (guidProperty != null)
                    guidValue = guidProperty.GetValue(privilege, null);
                else
                {
                    FieldInfo guidField = privilegeType.GetField("Guid", BindingFlags.Public | BindingFlags.Instance) ??
                        privilegeType.GetField("Id", BindingFlags.Public | BindingFlags.Instance);
                    if (guidField != null) guidValue = guidField.GetValue(privilege);
                }
                if (!(guidValue is Guid)) return false;
                MethodInfo check = engine.SecurityManager.GetType().GetMethod("IsPrivilegeGranted", BindingFlags.Public | BindingFlags.Instance, null, new[] { typeof(Guid) }, null);
                if (check == null) return false;
                return (bool)check.Invoke(engine.SecurityManager, new[] { guidValue });
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("Could not confirm SDK privilege " + fieldName + ": " + ex.GetType().Name);
                return false;
            }
        }

        // Returns an authoritative active-Visitor state based on the documented activation model.
        // On the tested Directory, Visitor.CheckinDate on an active pre-registered entity mirrors its
        // creation timestamp even though Security Desk offers the Check in action. Keep that raw field
        // visible for diagnostics, but do not interpret it as attendance until activation is specific.
        private static string GetActiveVisitorLifecycleState(Visitor visitor, out DateTime? checkinUtc)
        {
            checkinUtc = null;
            if (visitor == null || visitor.Status == null) return "unknown";
            string activationType = visitor.Status.ActivationType.ToString();
            DateTime? activationDate = visitor.Status.ActivationDate;
            if (String.Equals(activationType, "Never", StringComparison.OrdinalIgnoreCase) && !activationDate.HasValue)
                return "pre-registered";
            if (String.Equals(activationType, "SpecificDate", StringComparison.OrdinalIgnoreCase) && activationDate.HasValue)
            {
                checkinUtc = NormalizeSdkDateToUtc(activationDate.Value);
                return checkinUtc.HasValue ? "checked-in" : "unknown";
            }
            return "ambiguous";
        }

        private static DateTime? ReadActiveVisitorCheckinUtc(Guid visitorGuid)
        {
            using (var readEngine = ConnectToGenetec())
            {
                try
                {
                    var current = readEngine.GetEntity(visitorGuid) as Visitor;
                    if (current == null || current.IsDisposed) return null;
                    DateTime? checkinUtc;
                    string state = GetActiveVisitorLifecycleState(current, out checkinUtc);
                    return String.Equals(state, "checked-in", StringComparison.OrdinalIgnoreCase) ? checkinUtc : null;
                }
                finally { try { readEngine.LogOff(); } catch { } }
            }
        }

        private static ArchivedVisitor ReadArchivedVisitor(Guid originalVisitorGuid)
        {
            // IMPORTANT: the booking ledger stores the original active Visitor/Cardholder entity GUID.
            // GetArchivedVisitor(Guid) expects the archived-visit GUID from an archived VisitorQuery row,
            // which is a different identifier. For a known original Visitor GUID, the 5.11 SDK exposes
            // GetArchivedVisitorByCardholderId(originalVisitorGuid). Using the wrong key makes a completed
            // checkout look as though no archive exists and can strand the lifecycle ledger in an uncertain state.
            using (var readEngine = ConnectToGenetec())
            {
                try { return readEngine.VisitorManager.GetArchivedVisitorByCardholderId(originalVisitorGuid); }
                finally { try { readEngine.LogOff(); } catch { } }
            }
        }

        private static ArchivedVisitor ReadArchivedVisitorWithRetry(Guid originalVisitorGuid, int timeoutMilliseconds, out string lastDiagnostic)
        {
            DateTime deadlineUtc = DateTime.UtcNow.AddMilliseconds(timeoutMilliseconds);
            lastDiagnostic = "no archived Visitor returned for original Visitor/Cardholder GUID";
            do
            {
                try
                {
                    ArchivedVisitor archived = ReadArchivedVisitor(originalVisitorGuid);
                    if (archived != null) return archived;
                    lastDiagnostic = "archive lookup by original Visitor/Cardholder GUID returned null";
                }
                catch (SdkException ex)
                {
                    lastDiagnostic = "GetArchivedVisitorByCardholderId SdkException(" + ex.ErrorCode + "): " + ex.Message;
                    // Genetec SDK documents InvalidValue when no archived visit is found for the original Cardholder GUID.
                    // Retry that eventual-consistency condition only; permission/connectivity errors fail fast.
                    if (ex.ErrorCode != SdkError.InvalidValue) throw;
                }

                if (DateTime.UtcNow >= deadlineUtc) break;
                Thread.Sleep(500);
            }
            while (true);

            return null;
        }

        private static object ReadVisitorLifecycleStateSnapshot(string rawBookingId)
        {
            string bookingId = (rawBookingId ?? String.Empty).Trim().ToUpperInvariant();
            if (!IsAllowedSyntheticLifecycleBookingId(bookingId))
                throw new BridgeException(400, "NON_SYNTHETIC_BOOKING_REJECTED", "Read-only lifecycle inspection accepts only VMS-SDKTEST-* test harness IDs or prototype-generated VMS-UI-{six hex digits} IDs.");

            string entityKey = "STAMFORD VMS BOOKING " + bookingId;
            LifecycleLedger ledger = ReadLifecycleLedger();
            LifecycleBookingRecord bookingMapping = ledger.Bookings.FirstOrDefault(item =>
                String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase));
            Guid mappedGuid = Guid.Empty;
            if (bookingMapping != null) Guid.TryParse(bookingMapping.VisitorGuid, out mappedGuid);
            if (mappedGuid == Guid.Empty)
            {
                LifecycleEventRecord latestKnownEvent = ledger.Events.Where(item =>
                    String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase))
                    .OrderByDescending(item => item.CompletedAtUtc, StringComparer.Ordinal).FirstOrDefault();
                if (latestKnownEvent != null) Guid.TryParse(latestKnownEvent.VisitorGuid, out mappedGuid);
            }

            List<LifecycleEventRecord> savedEvents = ledger.Events.Where(item =>
                String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase))
                .OrderBy(item => item.EventAtUtc, StringComparer.Ordinal).ToList();
            LifecycleEventRecord savedCheckInEvent = savedEvents.FirstOrDefault(item => String.Equals(item.EventType, "CheckIn", StringComparison.Ordinal));
            string savedCheckInEventId = savedCheckInEvent == null ? null : savedCheckInEvent.EventId;
            LifecycleEventRecord savedCheckOutEvent = savedEvents.FirstOrDefault(item => String.Equals(item.EventType, "CheckOut", StringComparison.Ordinal));
            string savedCheckOutEventId = savedCheckOutEvent == null ? null : savedCheckOutEvent.EventId;
            bool savedCheckOutReconciledFromArchive = savedCheckOutEvent != null && savedCheckOutEvent.ReconciledFromArchive;

            string directoryVersion;
            using (var engine = ConnectToGenetec())
            {
                try
                {
                    directoryVersion = RequireVisitorManagementDirectory(engine);
                    List<Visitor> matches = FindVmsVisitorsByKey(engine, entityKey);
                    if (matches.Count > 1)
                        return new
                        {
                            success = true, mode = "read-only-visitor-lifecycle-state", bookingId = bookingId,
                            entityKey = entityKey, directoryVersion = directoryVersion, activeMatchCount = matches.Count,
                            duplicateDetected = true, activeVisitorFound = true, archivedVisitorFound = false,
                            writeAttempted = false, savedCheckInEventId = savedCheckInEventId,
                            savedCheckOutEventId = savedCheckOutEventId,
                            savedCheckOutReconciledFromArchive = savedCheckOutReconciledFromArchive,
                            nextAction = "Stop and reconcile duplicate native Visitor entities before any write."
                        };

                    if (matches.Count == 1)
                    {
                        Visitor active = engine.GetEntity(matches[0].Guid) as Visitor;
                        if (active == null || active.IsDisposed)
                            throw new BridgeException(503, "LIFECYCLE_STATE_ACTIVE_ENTITY_UNAVAILABLE", "Directory matched an active Visitor but the SDK could not load it.");
                        DateTime? canonicalCheckinUtc;
                        string activeState = GetActiveVisitorLifecycleState(active, out canonicalCheckinUtc);
                        return new
                        {
                            success = true, mode = "read-only-visitor-lifecycle-state", bookingId = bookingId,
                            entityKey = entityKey, directoryVersion = directoryVersion, activeMatchCount = 1,
                            duplicateDetected = false, activeVisitorFound = true, archivedVisitorFound = false,
                            visitorGuid = active.Guid.ToString("D"), entityName = active.Name,
                            activeLifecycleState = activeState,
                            activationType = active.Status == null ? "unknown" : active.Status.ActivationType.ToString(),
                            activationDateUtc = active.Status != null && active.Status.ActivationDate.HasValue
                                ? NormalizeSdkDateToUtc(active.Status.ActivationDate.Value)?.ToString("o") : null,
                            rawEntityCheckinDateUtc = NormalizeSdkDateToUtc(active.CheckinDate)?.ToString("o"),
                            canonicalCheckinDateUtc = canonicalCheckinUtc?.ToString("o"),
                            expectedArrivalLocal = NormalizeSdkDateToUtc(active.VisitDate)?.ToString("o"),
                            departureLocal = NormalizeSdkDateToUtc(active.Departure)?.ToString("o"),
                            hasCredentials = active.Credentials != null && active.Credentials.Any(),
                            savedCheckInEventId = savedCheckInEventId,
                            savedCheckOutEventId = savedCheckOutEventId,
                            savedCheckOutReconciledFromArchive = savedCheckOutReconciledFromArchive,
                            writeAttempted = false,
                            nextAction = activeState == "checked-in"
                                ? "Check-in is active. Only retry an existing EventId; do not issue another check-in event."
                                : (activeState == "pre-registered"
                                    ? "Visitor is still active and pre-registered. Review the failed operation stage before retrying."
                                    : "State is ambiguous. Inspect Security Desk and reconcile before any write.")
                        };
                    }
                }
                finally { try { engine.LogOff(); } catch { } }
            }

            if (mappedGuid != Guid.Empty)
            {
                try
                {
                    ArchivedVisitor archived = ReadArchivedVisitor(mappedGuid);
                    if (archived != null)
                    {
                        DateTime? archivedCheckoutUtc = NormalizeSdkDateToUtc(archived.CheckoutDate);
                        DateTime savedCheckOutUtc;
                        DateTime savedCheckOutCheckinUtc;
                        DateTime savedCheckInUtc;
                        Guid savedCheckOutVisitorGuid;
                        bool savedCheckOutCheckinMatchesVerifiedCheckIn = savedCheckOutEvent != null &&
                            savedCheckInEvent != null && savedCheckInEvent.ReadBackVerified &&
                            Guid.TryParse(savedCheckInEvent.VisitorGuid, out savedCheckOutVisitorGuid) && savedCheckOutVisitorGuid == mappedGuid &&
                            DateTime.TryParse(savedCheckInEvent.GenetecCheckinDateUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out savedCheckInUtc) &&
                            DateTime.TryParse(savedCheckOutEvent.GenetecCheckinDateUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out savedCheckOutCheckinUtc) &&
                            Math.Abs((savedCheckOutCheckinUtc.ToUniversalTime() - savedCheckInUtc.ToUniversalTime()).TotalSeconds) <= 2;
                        bool savedCheckOutMatchesArchive = savedCheckOutEvent != null && savedCheckOutEvent.ReadBackVerified &&
                            Guid.TryParse(savedCheckOutEvent.VisitorGuid, out savedCheckOutVisitorGuid) && savedCheckOutVisitorGuid == mappedGuid &&
                            archived.VisitState == VisitStates.CheckedOut && archivedCheckoutUtc.HasValue &&
                            DateTime.TryParse(savedCheckOutEvent.GenetecCheckoutDateUtc, CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out savedCheckOutUtc) &&
                            Math.Abs((savedCheckOutUtc.ToUniversalTime() - archivedCheckoutUtc.Value).TotalSeconds) <= 2 &&
                            savedCheckOutCheckinMatchesVerifiedCheckIn;
                        return new
                        {
                            success = true, mode = "read-only-visitor-lifecycle-state", bookingId = bookingId,
                            entityKey = entityKey, directoryVersion = directoryVersion, activeMatchCount = 0,
                            duplicateDetected = false, activeVisitorFound = false, archivedVisitorFound = true,
                            visitorGuid = mappedGuid.ToString("D"),
                            originalVisitorGuid = mappedGuid.ToString("D"),
                            archivedVisitGuid = archived.Guid.ToString("D"),
                            archiveLookupKey = "original-visitor-cardholder-guid",
                            archivedEntityName = archived.Name,
                            visitState = archived.VisitState.ToString(),
                            archivedCheckinDateUtc = NormalizeSdkDateToUtc(archived.CheckinDate)?.ToString("o"),
                            archivedCheckoutDateUtc = archivedCheckoutUtc?.ToString("o"),
                            archivedCheckinDateUtcDiagnostic = NormalizeSdkDateToUtc(archived.CheckinDate)?.ToString("o"),
                            savedCheckInEventId = savedCheckInEventId,
                            savedCheckInEventAtUtc = savedCheckInEvent == null ? null : savedCheckInEvent.EventAtUtc,
                            savedCheckInGenetecCheckinDateUtc = savedCheckInEvent == null ? null : savedCheckInEvent.GenetecCheckinDateUtc,
                            savedCheckOutEventId = savedCheckOutEventId,
                            savedCheckOutEventAtUtc = savedCheckOutEvent == null ? null : savedCheckOutEvent.EventAtUtc,
                            savedCheckOutGenetecCheckinDateUtc = savedCheckOutEvent == null ? null : savedCheckOutEvent.GenetecCheckinDateUtc,
                            savedCheckOutGenetecCheckoutDateUtc = savedCheckOutEvent == null ? null : savedCheckOutEvent.GenetecCheckoutDateUtc,
                            savedCheckOutReadBackVerified = savedCheckOutEvent == null ? (bool?)null : savedCheckOutEvent.ReadBackVerified,
                            savedCheckOutCheckinMatchesVerifiedCheckIn = savedCheckOutEvent == null ? (bool?)null : savedCheckOutCheckinMatchesVerifiedCheckIn,
                            savedCheckOutMatchesArchive = savedCheckOutEvent == null ? (bool?)null : savedCheckOutMatchesArchive,
                            savedCheckOutReconciledFromArchive = savedCheckOutReconciledFromArchive,
                            writeAttempted = false,
                            nextAction = archived.VisitState != VisitStates.CheckedOut
                                ? "An archived Visitor was found but is not confirmed CheckedOut. Inspect Security Desk and SDK state before any write."
                                : (savedCheckOutEvent == null
                                    ? "Visitor is archived as checked out but the local ledger has no checkout event. Do not send another checkout; reconcile the failed operation with the saved event ledger."
                                    : (savedCheckOutMatchesArchive
                                        ? "Visitor is archived as checked out and the saved checkout ledger matches the archived checkout plus the previously verified canonical check-in time. No further lifecycle mutation or reconciliation is needed."
                                        : "A checkout ledger event exists, but its read-back evidence does not fully match the archive and previously verified check-in time. Do not retry checkout; inspect and repair the local ledger under operator control."))
                        };
                    }
                }
                catch (SdkException ex)
                {
                    if (ex.ErrorCode != SdkError.InvalidValue) throw;
                }
            }

            return new
            {
                success = true, mode = "read-only-visitor-lifecycle-state", bookingId = bookingId,
                entityKey = entityKey, directoryVersion = directoryVersion, activeMatchCount = 0,
                duplicateDetected = false, activeVisitorFound = false, archivedVisitorFound = false,
                mappedVisitorGuid = mappedGuid == Guid.Empty ? null : mappedGuid.ToString("D"),
                savedCheckInEventId = savedCheckInEventId, savedCheckOutEventId = savedCheckOutEventId,
                            savedCheckOutReconciledFromArchive = savedCheckOutReconciledFromArchive,
                writeAttempted = false,
                nextAction = "No active Visitor or readable archive was found. Do not retry lifecycle writes until an authorised administrator reconciles the record."
            };
        }

        private static DateTime? NormalizeSdkDateToUtc(DateTime value)
        {
            if (value == DateTime.MinValue || value == DateTime.MaxValue) return null;
            if (value.Kind == DateTimeKind.Utc) return value;
            if (value.Kind == DateTimeKind.Local) return value.ToUniversalTime();
            // SDK visitor date properties follow the workstation's configured local timezone unless explicitly UTC.
            return DateTime.SpecifyKind(value, DateTimeKind.Local).ToUniversalTime();
        }

        private static bool IsLifecycleOperationMappingVerified()
        {
            try { return ReadLifecycleLedger().LifecycleOperationMappingVerified; }
            catch { return false; }
        }

        private static string GetLifecycleLedgerPath()
        {
            string basePath = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (String.IsNullOrWhiteSpace(basePath)) basePath = AppDomain.CurrentDomain.BaseDirectory;
            return Path.Combine(basePath, "StamfordVMS", "GenetecBridge", "visitor-lifecycle-ledger.json");
        }

        private static LifecycleLedger ReadLifecycleLedger()
        {
            lock (LifecycleLedgerLock)
            {
                string path = GetLifecycleLedgerPath();
                if (!System.IO.File.Exists(path)) return new LifecycleLedger();
                try
                {
                    LifecycleLedger ledger = Serializer.Deserialize<LifecycleLedger>(System.IO.File.ReadAllText(path, Encoding.UTF8));
                    if (ledger == null) throw new InvalidDataException("Lifecycle ledger deserialised as null.");
                    if (ledger.Bookings == null) ledger.Bookings = new List<LifecycleBookingRecord>();
                    if (ledger.Events == null) ledger.Events = new List<LifecycleEventRecord>();
                    return ledger;
                }
                catch (Exception ex)
                {
                    throw new BridgeException(503, "LIFECYCLE_LEDGER_UNREADABLE", "The local synthetic lifecycle ledger could not be read. No lifecycle mutation was attempted. Technical detail: " + ex.GetType().Name);
                }
            }
        }

        private static void MutateLifecycleLedger(Action<LifecycleLedger> mutation)
        {
            lock (LifecycleLedgerLock)
            {
                string path = GetLifecycleLedgerPath();
                string directory = Path.GetDirectoryName(path);
                if (!System.IO.Directory.Exists(directory)) System.IO.Directory.CreateDirectory(directory);
                LifecycleLedger ledger;
                if (!System.IO.File.Exists(path)) ledger = new LifecycleLedger();
                else
                {
                    try { ledger = Serializer.Deserialize<LifecycleLedger>(System.IO.File.ReadAllText(path, Encoding.UTF8)); }
                    catch (Exception ex) { throw new BridgeException(503, "LIFECYCLE_LEDGER_UNREADABLE", "The lifecycle ledger is corrupt/unreadable; no event was recorded. Technical detail: " + ex.GetType().Name); }
                    if (ledger == null) throw new BridgeException(503, "LIFECYCLE_LEDGER_UNREADABLE", "The lifecycle ledger is empty or invalid.");
                    if (ledger.Bookings == null) ledger.Bookings = new List<LifecycleBookingRecord>();
                    if (ledger.Events == null) ledger.Events = new List<LifecycleEventRecord>();
                }
                mutation(ledger);
                string temp = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
                try
                {
                    System.IO.File.WriteAllText(temp, Serializer.Serialize(ledger), new UTF8Encoding(false));
                    if (System.IO.File.Exists(path))
                    {
                        try { System.IO.File.Replace(temp, path, null); }
                        catch (PlatformNotSupportedException) { System.IO.File.Delete(path); System.IO.File.Move(temp, path); }
                    }
                    else System.IO.File.Move(temp, path);
                }
                catch (Exception ex)
                {
                    try { if (System.IO.File.Exists(temp)) System.IO.File.Delete(temp); } catch { }
                    throw new BridgeException(503, "LIFECYCLE_LEDGER_WRITE_FAILED", "The lifecycle event could not be durably recorded. Inspect Genetec before retrying. Technical detail: " + ex.GetType().Name);
                }
            }
        }

        private static void PersistBookingVisitorGuid(string bookingId, Guid visitorGuid)
        {
            MutateLifecycleLedger(store => AddOrUpdateBookingMapping(store, bookingId, visitorGuid));
        }

        private static void AddOrUpdateBookingMapping(LifecycleLedger store, string bookingId, Guid visitorGuid)
        {
            LifecycleBookingRecord existing = store.Bookings.FirstOrDefault(item => String.Equals(item.BookingId, bookingId, StringComparison.OrdinalIgnoreCase));
            if (existing == null)
            {
                store.Bookings.Add(new LifecycleBookingRecord { BookingId = bookingId, VisitorGuid = visitorGuid.ToString("D"), UpdatedAtUtc = DateTime.UtcNow.ToString("o") });
            }
            else
            {
                Guid oldGuid;
                if (Guid.TryParse(existing.VisitorGuid, out oldGuid) && oldGuid != visitorGuid)
                    throw new BridgeException(409, "BOOKING_VISITOR_GUID_CONFLICT", "The lifecycle ledger already maps this booking ID to a different Visitor GUID. Reconcile the mapping manually; no lifecycle event was sent.");
                existing.VisitorGuid = visitorGuid.ToString("D");
                existing.UpdatedAtUtc = DateTime.UtcNow.ToString("o");
            }
        }

        private static bool IsVisitorLifecycleSyncEnabled()
        {
            return String.Equals(Environment.GetEnvironmentVariable("GENETEC_ENABLE_VISITOR_LIFECYCLE_SYNC"), "1", StringComparison.Ordinal);
        }

        private static object LookupVmsVisitorByBookingId(string rawBookingId)
        {
            string bookingId = (rawBookingId ?? String.Empty).Trim().ToUpperInvariant();
            if (!Regex.IsMatch(bookingId, @"^VMS-[A-Z0-9-]{1,24}$", RegexOptions.CultureInvariant))
            {
                throw new BridgeException(400, "INVALID_BOOKING_ID",
                    "Only synthetic VMS booking references such as VMS-SDKTEST-01 can be looked up by this endpoint.");
            }

            using (var engine = ConnectToGenetec())
            {
                try
                {
                    var mainServer = engine.GetEntities(EntityType.Server)
                        .OfType<Server>()
                        .FirstOrDefault(server => server.IsMainServer);
                    if (mainServer == null)
                    {
                        throw new BridgeException(503, "DIRECTORY_SERVER_NOT_FOUND",
                            "Authenticated, but the main Directory server entity was not found. No write was attempted.");
                    }

                    string directoryVersion = mainServer.Version == null ? "unknown" : mainServer.Version.ToString();
                    if (!directoryVersion.StartsWith("5.11", StringComparison.OrdinalIgnoreCase))
                    {
                        throw new BridgeException(409, "DIRECTORY_VERSION_GUARD_FAILED",
                            "This diagnostic lookup is restricted to a Directory reporting version 5.11.");
                    }

                    var systemConfiguration = engine.GetEntity(SdkGuids.SystemConfiguration) as SystemConfiguration;
                    bool visitorManagementLicensed = systemConfiguration != null &&
                        systemConfiguration.License != null && systemConfiguration.License.IsVisitorManagementEnabled;
                    if (!visitorManagementLicensed)
                    {
                        throw new BridgeException(409, "VISITOR_MANAGEMENT_NOT_LICENSED",
                            "Security Center does not report Visitor Management as licensed. No lookup was performed.");
                    }

                    string entityKey = "STAMFORD VMS BOOKING " + bookingId;
                    List<Visitor> matches = FindVmsVisitorsByKey(engine, entityKey);
                    return new
                    {
                        success = true,
                        mode = "read-only-visitor-booking-lookup",
                        bookingId = bookingId,
                        entityKey = entityKey,
                        directoryVersion = directoryVersion,
                        matchCount = matches.Count,
                        duplicateDetected = matches.Count > 1,
                        matches = matches.Select(visitor =>
                        {
                            DateTime? nativeActivationCheckinUtc;
                            string activeLifecycleState = GetActiveVisitorLifecycleState(visitor, out nativeActivationCheckinUtc);
                            DateTime? rawEntityCheckinUtc = NormalizeSdkDateToUtc(visitor.CheckinDate);
                            return new
                            {
                                visitorGuid = visitor.Guid.ToString("D"),
                                entityName = visitor.Name,
                                firstName = visitor.FirstName,
                                lastName = visitor.LastName,
                                activationType = visitor.Status == null ? "unknown" : visitor.Status.ActivationType.ToString(),
                                activationDateUtc = visitor.Status != null && visitor.Status.ActivationDate.HasValue
                                    ? NormalizeSdkDateToUtc(visitor.Status.ActivationDate.Value).GetValueOrDefault().ToString("o") : null,
                                // Canonical active-visit check-in is based on activation state. On this 5.11 Directory,
                                // raw Visitor.CheckinDate can equal Creation date while the Security Desk Check in
                                // action remains available, so expose it separately rather than treating it as attendance.
                                checkinDateUtc = nativeActivationCheckinUtc.HasValue ? nativeActivationCheckinUtc.Value.ToString("o") : null,
                                rawEntityCheckinDateUtc = rawEntityCheckinUtc.HasValue ? rawEntityCheckinUtc.Value.ToString("o") : null,
                                activeLifecycleState = activeLifecycleState,
                                statusState = visitor.Status == null ? "unknown" : visitor.Status.State.ToString(),
                                expectedArrivalLocal = ToLocalClockForComparison(visitor.VisitDate).ToString("o"),
                                departureLocal = ToLocalClockForComparison(visitor.Departure).ToString("o"),
                                hasCredentials = visitor.Credentials != null && visitor.Credentials.Any()
                            };
                        }).ToArray(),
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeAttempted = false
                    };
                }
                finally
                {
                    try { engine.LogOff(); } catch { /* Best-effort session close. */ }
                }
            }
        }

        private static List<Visitor> FindVmsVisitorsByKey(Engine engine, string entityKey)
        {
            var query = engine.ReportManager.CreateReportQuery(ReportType.EntityConfiguration) as EntityConfigurationQuery;
            if (query == null)
            {
                throw new BridgeException(503, "VISITOR_ENTITY_QUERY_UNAVAILABLE",
                    "The SDK did not create an EntityConfigurationQuery. No create/update decision was made.");
            }

            // This query is executed against the Directory, unlike Engine.GetEntities(), which is
            // cache-only. Match the prototype-owned stable entity key exactly and restrict to Visitors.
            query.EntityTypeFilter.Add(EntityType.Visitor);
            query.Name = entityKey;
            query.NameSearchMode = StringSearchMode.Is;
            query.MaximumResultCount = 100;
            query.StrictResults = true;

            var result = query.Query();
            if (result == null || !result.Success || result.Data == null)
            {
                throw new BridgeException(503, "VISITOR_ENTITY_QUERY_FAILED",
                    "The Directory-backed exact-name Visitor query failed. No create/update was attempted.");
            }

            var matches = new List<Visitor>();
            foreach (DataRow row in result.Data.Rows)
            {
                Guid entityGuid = Guid.Empty;
                // SDK report columns can differ by report/layout. Prefer a named Guid column, then
                // fall back to scanning typed row values rather than assuming column zero.
                if (row.Table.Columns.Contains("Guid") && row["Guid"] is Guid)
                {
                    entityGuid = (Guid)row["Guid"];
                }
                else
                {
                    foreach (object value in row.ItemArray)
                    {
                        if (value is Guid)
                        {
                            entityGuid = (Guid)value;
                            break;
                        }
                        Guid parsed;
                        if (value is string && Guid.TryParse((string)value, out parsed))
                        {
                            entityGuid = parsed;
                            break;
                        }
                    }
                }
                if (entityGuid == Guid.Empty) continue;

                var candidate = engine.GetEntity(entityGuid) as Visitor;
                if (candidate != null && !candidate.IsDisposed &&
                    String.Equals(candidate.Name, entityKey, StringComparison.OrdinalIgnoreCase) &&
                    !matches.Any(existing => existing.Guid == candidate.Guid))
                {
                    matches.Add(candidate);
                }
            }

            return matches;
        }

        private static string ValidateVisitorField(string value, string label, int maxLength)
        {
            string clean = (value ?? String.Empty).Trim();
            if (clean.Length == 0 || clean.Length > maxLength || clean.Any(Char.IsControl))
            {
                throw new BridgeException(400, "INVALID_VISITOR_FIELD", "The visitor " + label + " is missing, too long or contains unsupported control characters.");
            }
            return clean;
        }

        private static void ParseVisitWindow(string visitDate, string window, out DateTime arrivalLocal, out DateTime departureLocal)
        {
            DateTime localVisitDate;
            if (!DateTime.TryParseExact((visitDate ?? String.Empty).Trim(), "yyyy-MM-dd",
                CultureInfo.InvariantCulture, DateTimeStyles.None, out localVisitDate))
            {
                throw new BridgeException(400, "INVALID_VISIT_DATE", "Visit date must use the YYYY-MM-DD format.");
            }
            localVisitDate = localVisitDate.Date;
            if (localVisitDate < DateTime.Today || localVisitDate > DateTime.Today.AddDays(365))
            {
                throw new BridgeException(400, "VISIT_DATE_OUT_OF_RANGE", "For this prototype, a visitor booking must be dated today through the next 365 days.");
            }

            // Browser input can contain visually identical Unicode dashes, full-width
            // punctuation, non-breaking spaces, or invisible format characters. Normalize
            // before parsing so the UI's displayed 09:00–10:00 cannot fail just because
            // the separator code point differs from ASCII hyphen-minus.
            string normalizedWindow = (window ?? String.Empty).Normalize(NormalizationForm.FormKC);
            normalizedWindow = Regex.Replace(normalizedWindow, @"[\u200B-\u200D\u2060\uFEFF]", String.Empty);
            normalizedWindow = Regex.Replace(normalizedWindow, @"[\u00A0\u1680\u2000-\u200A\u202F\u205F\u3000]", " ");
            normalizedWindow = Regex.Replace(normalizedWindow, @"[\p{Pd}\u2212]", "-");
            normalizedWindow = normalizedWindow.Trim();
            Match match = Regex.Match(normalizedWindow,
                @"^([0-9]{1,2}:[0-9]{2})\s*-\s*([0-9]{1,2}:[0-9]{2})$", RegexOptions.CultureInvariant);
            if (!match.Success)
            {
                throw new BridgeException(400, "INVALID_VISIT_WINDOW",
                    "Visit window must use a start and end clock time, for example 09:00-10:00. Unicode dash/space variants are normalised automatically.");
            }

            DateTime startTime;
            DateTime endTime;
            string[] formats = new[] { "H:mm", "HH:mm" };
            if (!DateTime.TryParseExact(match.Groups[1].Value, formats, CultureInfo.InvariantCulture, DateTimeStyles.None, out startTime) ||
                !DateTime.TryParseExact(match.Groups[2].Value, formats, CultureInfo.InvariantCulture, DateTimeStyles.None, out endTime))
            {
                throw new BridgeException(400, "INVALID_VISIT_WINDOW", "Visit window contains an invalid clock time.");
            }

            arrivalLocal = localVisitDate.Add(startTime.TimeOfDay);
            departureLocal = localVisitDate.Add(endTime.TimeOfDay);
            if (departureLocal <= arrivalLocal) departureLocal = departureLocal.AddDays(1);
            if (departureLocal <= arrivalLocal || (departureLocal - arrivalLocal).TotalHours > 18)
            {
                throw new BridgeException(400, "INVALID_VISIT_WINDOW", "Visit window must be positive and no longer than 18 hours for this prototype.");
            }
            DateTime bridgeNow = DateTime.Now;
            // An invited booking may be synced even when its scheduled start time has arrived
            // or passed, provided the appointment has not expired. This operation writes the
            // planned VisitDate/Departure and explicitly keeps native activation at Never; it
            // does NOT record attendance. Actual check-in is handled only by the separate,
            // guarded visitor-lifecycle endpoint after the VMS has recorded a check-in event.
            // Requiring arrival > now+1 minute made normal same-day invitations fail merely
            // because the operator synced them close to/after the booked start time.
            if (departureLocal <= bridgeNow)
            {
                throw new BridgeException(409, "VISIT_WINDOW_EXPIRED", "This invited booking's scheduled visit window has ended according to the bridge computer's clock. No invitation sync was attempted. If a real check-in/check-out was already recorded, use the separate lifecycle synchronization/reconciliation path; do not use invitation sync to record attendance.");
            }
        }

        private static bool VisitorGuidExistsInServerQuery(Engine engine, Guid visitorGuid)
        {
            var query = engine.ReportManager.CreateReportQuery(ReportType.Visitor) as VisitorQuery;
            if (query == null)
            {
                throw new BridgeException(503, "VISITOR_QUERY_UNAVAILABLE",
                    "The SDK did not create a VisitorQuery. The write result cannot be independently confirmed by a server-side Visitor report.");
            }

            PropertyInfo filterProperty = typeof(VisitorQuery).GetProperty("EntityGuids", BindingFlags.Instance | BindingFlags.Public);
            object filter = filterProperty == null ? null : filterProperty.GetValue(query, null);
            MethodInfo addGuid = filter == null ? null : filter.GetType().GetMethod("Add", new[] { typeof(Guid) });
            if (addGuid == null)
            {
                throw new BridgeException(503, "VISITOR_QUERY_FILTER_UNAVAILABLE",
                    "The installed SDK did not expose a supported EntityGuids filter collection for VisitorQuery. No success will be reported without server-side verification.");
            }
            addGuid.Invoke(filter, new object[] { visitorGuid });

            var result = query.Query();
            if (result == null || !result.Success || result.Data == null)
            {
                throw new BridgeException(503, "VISITOR_REPORT_QUERY_FAILED",
                    "The follow-up Visitor report query did not complete successfully. Verify Genetec before retrying.");
            }

            // Do not assume the report's first column is the entity GUID; report layouts can vary by SDK/build.
            // Scan returned values for the exact GUID and accept both native Guid and parseable string representations.
            return result.Data.Rows.Cast<DataRow>().Any(row => row.ItemArray.Any(value =>
                (value is Guid && (Guid)value == visitorGuid) ||
                (value is string && Guid.TryParse((string)value, out Guid parsedGuid) && parsedGuid == visitorGuid)));
        }

        private static DateTime ToLocalClockForComparison(DateTime value)
        {
            if (value.Kind == DateTimeKind.Utc) return value.ToLocalTime();
            if (value.Kind == DateTimeKind.Unspecified) return DateTime.SpecifyKind(value, DateTimeKind.Local);
            return value;
        }

        private static bool SameLocalDate(DateTime actual, DateTime expected)
        {
            actual = ToLocalClockForComparison(actual);
            return actual.Year == expected.Year && actual.Month == expected.Month && actual.Day == expected.Day;
        }

        private static bool SameLocalMinute(DateTime actual, DateTime expected)
        {
            actual = ToLocalClockForComparison(actual);
            return actual.Year == expected.Year && actual.Month == expected.Month && actual.Day == expected.Day &&
                actual.Hour == expected.Hour && actual.Minute == expected.Minute;
        }

        private static void SetRequiredStringProperty(object target, string propertyName, string value)
        {
            PropertyInfo property = target.GetType().GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.FlattenHierarchy);
            if (property == null || !property.CanWrite || property.PropertyType != typeof(string))
            {
                throw new BridgeException(409, "VISITOR_FIELD_NOT_SUPPORTED",
                    "The installed SDK does not expose a writable string property named " + propertyName + " on the native Visitor type. No claim of successful field mapping can be made.");
            }
            property.SetValue(target, value, null);
        }

        private static string GetStringProperty(object target, string propertyName)
        {
            PropertyInfo property = target.GetType().GetProperty(propertyName, BindingFlags.Instance | BindingFlags.Public | BindingFlags.FlattenHierarchy);
            return property == null ? null : Convert.ToString(property.GetValue(target, null), CultureInfo.InvariantCulture);
        }

        private static object CreateOrFindTestCardholder()
        {
            if (!IsTestWriteEnabled())
            {
                throw new BridgeException(403, "TEST_WRITES_DISABLED",
                    "The bridge is in read-only mode. Restart it and explicitly enable the guarded test-cardholder operation for an approved non-production test environment.");
            }

            string operationId = Guid.NewGuid().ToString("D");
            using (var engine = ConnectToGenetec())
            {
                try
                {
                    var mainServer = engine.GetEntities(EntityType.Server)
                        .OfType<Server>()
                        .FirstOrDefault(s => s.IsMainServer);
                    if (mainServer == null)
                    {
                        throw new BridgeException(503, "DIRECTORY_SERVER_NOT_FOUND", "Authenticated, but the main Directory server entity was not found.");
                    }

                    string directoryVersion = mainServer.Version == null ? "unknown" : mainServer.Version.ToString();
                    if (!directoryVersion.StartsWith("5.11", StringComparison.OrdinalIgnoreCase))
                    {
                        throw new BridgeException(409, "DIRECTORY_VERSION_GUARD_FAILED",
                            "The live-write test is restricted to a Directory version reported as 5.11. No write was attempted.");
                    }

                    Cardholder existing = FindTestCardholder(engine);
                    if (existing != null)
                    {
                        return new
                        {
                            success = true,
                            mode = "guarded-test-write",
                            operation = "create-only-test-cardholder",
                            created = false,
                            alreadyExisted = true,
                            cardholderName = existing.Name,
                            firstName = existing.FirstName,
                            lastName = existing.LastName,
                            cardholderGuid = existing.Guid.ToString("D"),
                            directoryVersion = directoryVersion,
                            credentialCreated = false,
                            accessRightsChanged = false,
                            message = "The dedicated prototype test cardholder already exists; no changes were made.",
                            operationId = operationId,
                            completedAtUtc = DateTime.UtcNow.ToString("o"),
                            writeOperationsEnabled = true
                        };
                    }

                    // Narrowly scoped write: fixed synthetic identity, no credential, no group, no access policy.
                    // This reuses the creation pattern from the supplied Genetec sync project.
                    var cardholder = engine.CreateEntity(TestEntityKey, EntityType.Cardholder) as Cardholder;
                    if (cardholder == null)
                    {
                        throw new BridgeException(503, "TEST_CARDHOLDER_CREATE_RETURNED_NULL",
                            "Genetec did not return a Cardholder entity. Verify SDK support and account permissions.");
                    }

                    cardholder.FirstName = TestFirstName;
                    cardholder.LastName = TestLastName;
                    Guid createdGuid = cardholder.Guid;

                    // Read back through the Configuration report before we claim success.
                    Cardholder verified = FindTestCardholder(engine);
                    if (verified == null || verified.Guid != createdGuid)
                    {
                        throw new BridgeException(503, "TEST_CARDHOLDER_READBACK_FAILED",
                            "A create request was issued, but the new test cardholder could not be verified by read-back. Check Genetec before retrying.");
                    }

                    Console.WriteLine("GUARDED TEST WRITE: operation=" + operationId + " created fixed synthetic cardholder " + verified.Guid.ToString("D"));
                    return new
                    {
                        success = true,
                        mode = "guarded-test-write",
                        operation = "create-only-test-cardholder",
                        created = true,
                        alreadyExisted = false,
                        cardholderName = verified.Name,
                        firstName = verified.FirstName,
                        lastName = verified.LastName,
                        cardholderGuid = verified.Guid.ToString("D"),
                        directoryVersion = directoryVersion,
                        credentialCreated = false,
                        accessRightsChanged = false,
                        message = "The fixed synthetic test cardholder was created and verified by read-back.",
                        operationId = operationId,
                        completedAtUtc = DateTime.UtcNow.ToString("o"),
                        writeOperationsEnabled = true
                    };
                }
                catch (BridgeException)
                {
                    throw;
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Create test cardholder failed: operation=" + operationId + " " + ex.GetType().Name + " — " + ex.Message);
                    throw new BridgeException(503, "TEST_CARDHOLDER_CREATE_FAILED",
                        "The test cardholder operation did not complete successfully. Review bridge logs and verify Genetec before attempting again.");
                }
                finally
                {
                    try { engine.LogOff(); } catch { /* Best-effort session close. */ }
                }
            }
        }

        private static Cardholder FindTestCardholder(Engine engine)
        {
            var query = engine.ReportManager.CreateReportQuery(ReportType.CardholderConfiguration) as EntityConfigurationQuery;
            if (query == null)
            {
                throw new BridgeException(503, "CARDHOLDER_QUERY_UNAVAILABLE", "The SDK did not create a cardholder configuration query.");
            }

            var result = query.Query();
            if (result == null || !result.Success || result.Data == null)
            {
                throw new BridgeException(503, "CARDHOLDER_QUERY_FAILED", "The cardholder configuration query failed; no write was attempted.");
            }

            foreach (DataRow row in result.Data.Rows)
            {
                if (row.ItemArray.Length == 0 || !(row[0] is Guid)) continue;
                var cardholder = engine.GetEntity((Guid)row[0]) as Cardholder;
                if (cardholder == null) continue;

                bool fixedKeyMatches = String.Equals(cardholder.Name, TestEntityKey, StringComparison.OrdinalIgnoreCase);
                bool fixedDisplayNameMatches =
                    String.Equals(cardholder.FirstName, TestFirstName, StringComparison.OrdinalIgnoreCase) &&
                    String.Equals(cardholder.LastName, TestLastName, StringComparison.OrdinalIgnoreCase);
                if (fixedKeyMatches || fixedDisplayNameMatches) return cardholder;
            }

            return null;
        }

        private static Engine ConnectToGenetec()
        {
            string server = Environment.GetEnvironmentVariable("GENETEC_SERVER");
            string username = Environment.GetEnvironmentVariable("GENETEC_USERNAME");
            string password = Environment.GetEnvironmentVariable("GENETEC_PASSWORD");
            string cert = Environment.GetEnvironmentVariable("GENETEC_CERT");
            string portText = Environment.GetEnvironmentVariable("GENETEC_PORT");
            int port;
            if (!Int32.TryParse(portText, out port) || port < 1 || port > 65535) port = 5500;

            if (String.IsNullOrWhiteSpace(server) || String.IsNullOrWhiteSpace(username) ||
                String.IsNullOrWhiteSpace(password) || String.IsNullOrWhiteSpace(cert))
            {
                throw new BridgeException(503, "CONFIGURATION_MISSING",
                    "Connection settings are missing from this bridge process. Restart it using the secure runtime prompt.");
            }

            var engine = new Engine();
            var loginResult = new TaskCompletionSource<LoginOutcome>();
            engine.LoginManager.LoggedOn += (sender, e) => loginResult.TrySetResult(new LoginOutcome(true, null));
            engine.LoginManager.LogonFailed += (sender, e) => loginResult.TrySetResult(new LoginOutcome(false, "GENETEC_LOGIN_FAILED"));

            try
            {
                engine.LoginManager.LogOn(server, username, password, port, cert);
                Task completed = Task.WhenAny(loginResult.Task, Task.Delay(TimeSpan.FromSeconds(25))).GetAwaiter().GetResult();
                if (completed != loginResult.Task)
                {
                    throw new BridgeException(504, "GENETEC_LOGIN_TIMEOUT", "Timed out waiting for a Genetec login result.");
                }

                LoginOutcome outcome = loginResult.Task.GetAwaiter().GetResult();
                if (!outcome.Success)
                {
                    throw new BridgeException(503, "GENETEC_LOGIN_FAILED", "Genetec rejected or could not complete the configured login. Review bridge logs and account permissions.");
                }
                return engine;
            }
            catch
            {
                try { ((IDisposable)engine).Dispose(); } catch { }
                throw;
            }
        }

        private static bool IsTestWriteEnabled()
        {
            return String.Equals(Environment.GetEnvironmentVariable("GENETEC_ENABLE_TEST_WRITES"), "1", StringComparison.Ordinal);
        }

        private static bool IsVisitorSyncEnabled()
        {
            return String.Equals(Environment.GetEnvironmentVariable("GENETEC_ENABLE_VISITOR_SYNC"), "1", StringComparison.Ordinal);
        }

        private static WriteRequest ReadWriteRequest(HttpListenerRequest request)
        {
            if (request.ContentLength64 > 2048)
            {
                throw new BridgeException(413, "REQUEST_TOO_LARGE", "The test-write request body is too large.");
            }
            if (String.IsNullOrWhiteSpace(request.ContentType) ||
                !request.ContentType.StartsWith("application/json", StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(415, "JSON_REQUIRED", "Content-Type application/json is required.");
            }

            using (var reader = new StreamReader(request.InputStream, request.ContentEncoding ?? Encoding.UTF8))
            {
                var builder = new StringBuilder();
                var buffer = new char[512];
                int read;
                while ((read = reader.Read(buffer, 0, buffer.Length)) > 0)
                {
                    if (builder.Length + read > 2048)
                    {
                        throw new BridgeException(413, "REQUEST_TOO_LARGE", "The test-write request body is too large.");
                    }
                    builder.Append(buffer, 0, read);
                }

                try { return Serializer.Deserialize<WriteRequest>(builder.ToString()); }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Invalid test-write request JSON: " + ex.GetType().Name);
                    throw new BridgeException(400, "INVALID_JSON", "The test-write request was not valid JSON.");
                }
            }
        }

        private static VisitorSyncRequest ReadVisitorSyncRequest(HttpListenerRequest request)
        {
            if (request.ContentLength64 > 8192)
            {
                throw new BridgeException(413, "REQUEST_TOO_LARGE", "The visitor-sync request body is too large.");
            }
            if (String.IsNullOrWhiteSpace(request.ContentType) ||
                !request.ContentType.StartsWith("application/json", StringComparison.OrdinalIgnoreCase))
            {
                throw new BridgeException(415, "JSON_REQUIRED", "Content-Type application/json is required.");
            }

            using (var reader = new StreamReader(request.InputStream, request.ContentEncoding ?? Encoding.UTF8))
            {
                var builder = new StringBuilder();
                var buffer = new char[1024];
                int read;
                while ((read = reader.Read(buffer, 0, buffer.Length)) > 0)
                {
                    if (builder.Length + read > 8192)
                    {
                        throw new BridgeException(413, "REQUEST_TOO_LARGE", "The visitor-sync request body is too large.");
                    }
                    builder.Append(buffer, 0, read);
                }
                try { return Serializer.Deserialize<VisitorSyncRequest>(builder.ToString()); }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Invalid visitor-sync JSON: " + ex.GetType().Name);
                    throw new BridgeException(400, "INVALID_JSON", "The visitor-sync request was not valid JSON.");
                }
            }
        }

        private static VisitorLifecycleRequest ReadVisitorLifecycleRequest(HttpListenerRequest request)
        {
            if (request.ContentLength64 > 8192)
                throw new BridgeException(413, "REQUEST_TOO_LARGE", "The visitor lifecycle request body is too large.");
            if (String.IsNullOrWhiteSpace(request.ContentType) || !request.ContentType.StartsWith("application/json", StringComparison.OrdinalIgnoreCase))
                throw new BridgeException(415, "JSON_REQUIRED", "Content-Type application/json is required.");
            using (var reader = new StreamReader(request.InputStream, request.ContentEncoding ?? Encoding.UTF8))
            {
                var builder = new StringBuilder();
                var buffer = new char[1024];
                int read;
                while ((read = reader.Read(buffer, 0, buffer.Length)) > 0)
                {
                    if (builder.Length + read > 8192) throw new BridgeException(413, "REQUEST_TOO_LARGE", "The visitor lifecycle request body is too large.");
                    builder.Append(buffer, 0, read);
                }
                try { return Serializer.Deserialize<VisitorLifecycleRequest>(builder.ToString()); }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Invalid visitor lifecycle JSON: " + ex.GetType().Name);
                    throw new BridgeException(400, "INVALID_JSON", "The visitor lifecycle request was not valid JSON.");
                }
            }
        }

        private static void WriteJson(HttpListenerResponse response, int statusCode, object payload)
        {
            byte[] buffer = Encoding.UTF8.GetBytes(Serializer.Serialize(payload));
            response.StatusCode = statusCode;
            response.ContentType = "application/json; charset=utf-8";
            response.ContentEncoding = Encoding.UTF8;
            response.ContentLength64 = buffer.Length;
            try
            {
                using (var output = response.OutputStream)
                {
                    output.Write(buffer, 0, buffer.Length);
                }
            }
            catch (HttpListenerException) { }
        }

        private static Assembly ResolveSdkAssembly(object sender, ResolveEventArgs args)
        {
            try
            {
                string name = new AssemblyName(args.Name).Name + ".dll";
                string path = System.IO.Path.Combine(_sdkDirectory ?? String.Empty, name);
                if (System.IO.File.Exists(path)) return Assembly.LoadFrom(path);
            }
            catch { /* Return null so the runtime reports the unresolved dependency. */ }
            return null;
        }

        private sealed class WriteRequest
        {
            public string Confirmation { get; set; }
        }

        private sealed class VisitorSyncRequest
        {
            public string Confirmation { get; set; }
            public string BookingId { get; set; }
            public string FirstName { get; set; }
            public string LastName { get; set; }
            public string VisitDate { get; set; }
            public string VisitWindow { get; set; }
            public string Status { get; set; }
        }

        private sealed class VisitorLifecycleRequest
        {
            public string Confirmation { get; set; }
            public string BookingId { get; set; }
            public string EventId { get; set; }
            public string EventType { get; set; }
            public string EventAtUtc { get; set; }
            public string VmsStatus { get; set; }
        }

        private sealed class LifecycleLedger
        {
            public bool LifecycleOperationMappingVerified { get; set; }
            public List<LifecycleBookingRecord> Bookings { get; set; }
            public List<LifecycleEventRecord> Events { get; set; }
            public LifecycleLedger()
            {
                Bookings = new List<LifecycleBookingRecord>();
                Events = new List<LifecycleEventRecord>();
            }
        }

        private sealed class LifecycleBookingRecord
        {
            public string BookingId { get; set; }
            public string VisitorGuid { get; set; }
            public string UpdatedAtUtc { get; set; }
        }

        private sealed class LifecycleEventRecord
        {
            public string BookingId { get; set; }
            public string EventId { get; set; }
            public string EventType { get; set; }
            public string EventAtUtc { get; set; }
            public string VmsStatus { get; set; }
            public string VisitorGuid { get; set; }
            public string OperationId { get; set; }
            public string GenetecCheckinDateUtc { get; set; }
            public string GenetecCheckoutDateUtc { get; set; }
            public bool ReadBackVerified { get; set; }
            public string CompletedAtUtc { get; set; }
            public bool ReconciledFromArchive { get; set; }
            public string ReconciliationNote { get; set; }
        }

        private sealed class LoginOutcome
        {
            public bool Success { get; private set; }
            public string ErrorCode { get; private set; }
            public LoginOutcome(bool success, string errorCode) { Success = success; ErrorCode = errorCode; }
        }

        private sealed class BridgeException : Exception
        {
            public int HttpStatusCode { get; private set; }
            public string Code { get; private set; }
            public BridgeException(int statusCode, string code, string message) : base(message)
            { HttpStatusCode = statusCode; Code = code; }
        }
    }
}

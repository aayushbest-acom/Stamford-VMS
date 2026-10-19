using System;
using System.Collections.Generic;
using System.Data;
using System.Diagnostics;
using System.IO;
using System.Globalization;
using System.Drawing;
using System.Drawing.Imaging;
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
        private const string BridgeBuild = "5.2.8";
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
        private static bool _enableTestCardholderWrites;
        private static bool _enableVisitorSync;
        private static bool _enableVisitorLifecycleSync;
        private static bool _enableCardholderFaceSync;
        private static bool _confirmBiometricPolicyApproved;
        private static bool _confirmGenetecCardholderFaceSyncApproved;

        private static int Main(string[] args)
        {
            if (args != null && args.Length > 0 &&
                String.Equals(args[0], "--visitor-booking-worker", StringComparison.OrdinalIgnoreCase))
            {
                return RunVisitorBookingWorker(args.Skip(1).ToArray());
            }

            // A CLR exception outside the request loop must leave a useful local trace. This does
            // not intercept native fail-fast/AccessViolation crashes, but it avoids losing managed
            // exception details when an SDK callback escapes its expected handler.
            AppDomain.CurrentDomain.UnhandledException += (sender, eventArgs) =>
            {
                try
                {
                    Console.Error.WriteLine("FATAL unhandled CLR exception; terminating=" + eventArgs.IsTerminating + ": " + eventArgs.ExceptionObject);
                    Console.Error.Flush();
                }
                catch { /* Never throw from the process-level diagnostic hook. */ }
            };

            try
            {
                ConfigureStartupScopes(args);
            }
            catch (ArgumentException ex)
            {
                Console.Error.WriteLine("Invalid Genetec bridge startup scopes: " + ex.Message);
                return 64;
            }

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
                    Console.WriteLine("Approved read-only Cardholder search: GET /api/genetec/cardholders?search=Name&limit=25 (feature and privacy gates required)");
                    Console.WriteLine("Approved bulk student-roster metadata import: GET /api/genetec/student-roster?externalIdPrefix=STU-&limit=1000 (prefix-filtered; portrait bytes excluded)");
                    Console.WriteLine("Approved read-only Cardholder photo/credential references: GET /api/genetec/cardholder-photo?cardholderGuid=<GUID> (photo never saved by bridge)");
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
                    Console.WriteLine(IsCardholderFaceSyncApproved()
                        ? "GUARDED CARDHOLDER PHOTO LOOKUP ENABLED by explicit command-line policy acknowledgements; read-only and bounded."
                        : "Cardholder photo/credential-reference lookup remains disabled until the explicit school policy acknowledgement switches are supplied.");
                    Console.WriteLine("Feature scopes are passed as command-line switches; no GENETEC_ENABLE_* environment variables are used.");
                    Console.WriteLine("Press Ctrl+C to stop.");

                    while (listener.IsListening)
                    {
                        HttpListenerContext context;
                        try { context = listener.GetContext(); }
                        catch (HttpListenerException) { break; }
                        catch (ObjectDisposedException) { break; }
                        // Isolate every HTTP request. A managed exception in a response serializer,
                        // an SDK wrapper, or a malformed request must not unwind the listener loop and
                        // take the bridge offline after a Visitor write has already committed.
                        HandleSafely(context);
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

        private static void HandleSafely(HttpListenerContext context)
        {
            string requestId = Guid.NewGuid().ToString("N");
            string method = "unknown";
            string path = "/";
            try
            {
                if (context != null && context.Request != null)
                {
                    method = context.Request.HttpMethod ?? "unknown";
                    path = context.Request.Url == null ? "/" : context.Request.Url.AbsolutePath;
                }
            }
            catch { /* Request metadata is diagnostic only. */ }

            Console.WriteLine("HTTP request start: requestId=" + requestId + " method=" + method + " path=" + path);
            Console.Out.Flush();
            try
            {
                Handle(context);
            }
            catch (Exception ex)
            {
                // Log stack and inner-exception details locally; never include request body,
                // visitor names, credentials, or photo bytes in these diagnostics.
                Console.Error.WriteLine("Unhandled HTTP request exception: requestId=" + requestId +
                    " method=" + method + " path=" + path + " exception=" + ex);
                Console.Error.Flush();
                try
                {
                    WriteJson(context.Response, 500, new
                    {
                        success = false,
                        errorCode = "BRIDGE_REQUEST_HANDLER_FAILED",
                        requestId = requestId,
                        error = "The bridge could not finish this request. Check the local bridge log and verify Genetec before retrying."
                    });
                }
                catch (Exception responseException)
                {
                    Console.Error.WriteLine("Could not send request failure response: requestId=" + requestId + " exception=" + responseException);
                    try { context.Response.Abort(); } catch { }
                }
            }
            finally
            {
                try
                {
                    Console.WriteLine("HTTP request complete: requestId=" + requestId + " method=" + method +
                        " path=" + path + " status=" + context.Response.StatusCode.ToString(CultureInfo.InvariantCulture));
                    Console.Out.Flush();
                }
                catch { /* Response status may be inaccessible after a client disconnect. */ }
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
            response.Headers["Access-Control-Allow-Headers"] = "Content-Type, X-Stamford-CSRF";
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
                bool visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled();
                payload = new
                {
                    service = "Stamford VMS Genetec Bridge",
                    bridge = "online",
                    bridgeBuild = BridgeBuild,
                    bridgeExecutablePath = Assembly.GetExecutingAssembly().Location,
                    mode = IsAnyWriteScopeEnabled() ? "read-only-with-separately-guarded-write-scopes" : "read-only",
                    sdkDirectoryConfigured = !String.IsNullOrWhiteSpace(_sdkDirectory) && System.IO.Directory.Exists(_sdkDirectory),
                    writeOperationsEnabled = IsAnyWriteScopeEnabled(),
                    testCardholderWriteEnabled = testCardholderWriteEnabled,
                    visitorSyncEnabled = visitorSyncEnabled,
                    visitorLifecycleSyncEnabled = visitorLifecycleSyncEnabled,
                    cardholderFaceSyncEnabled = IsCardholderFaceSyncApproved(),
                    writeScope = BuildEnabledWriteScopeDescription(),
                    lifecycleScope = visitorLifecycleSyncEnabled ? "synthetic-native-visitor-checkin-checkout-only" : "disabled"
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
                        writeOperationsEnabled = IsAnyWriteScopeEnabled(),
                        writeScope = BuildEnabledWriteScopeDescription(),
                        visitorManagementLicensed = false,
                        visitorSyncEnabled = IsVisitorSyncEnabled(),
                        visitorSyncScope = IsVisitorSyncEnabled() ? "enabled-but-licence-or-session-not-verified" : "disabled",
                        visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled(),
                        cardholderFaceSyncEnabled = IsCardholderFaceSyncApproved()
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
                        writeOperationsEnabled = IsAnyWriteScopeEnabled(),
                        writeScope = BuildEnabledWriteScopeDescription(),
                        visitorManagementLicensed = false,
                        visitorSyncEnabled = IsVisitorSyncEnabled(),
                        visitorSyncScope = IsVisitorSyncEnabled() ? "enabled-but-licence-or-session-not-verified" : "disabled",
                        visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled(),
                        cardholderFaceSyncEnabled = IsCardholderFaceSyncApproved()
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
                String.Equals(path, "/api/genetec/cardholders", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    payload = ReadGenetecCardholders(request.QueryString["search"], request.QueryString["limit"]);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new { success = false, errorCode = ex.Code, error = ex.Message, checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Read-only Cardholder search failed: " + ex.GetType().Name);
                    statusCode = 503;
                    payload = new { success = false, errorCode = "CARDHOLDER_SEARCH_FAILED", error = "Could not read Cardholder metadata from Genetec. Check bridge logs; no Genetec write was attempted.", checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
            }
            else if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/student-roster-groups", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    payload = ReadGenetecStudentGroupCatalog();
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new { success = false, errorCode = ex.Code, error = ex.Message, checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Read-only Cardholder group catalog failed: " + ex.GetType().Name);
                    statusCode = 503;
                    payload = new { success = false, errorCode = "CARDHOLDER_GROUP_CATALOG_FAILED", error = "Could not read Cardholder group membership through this SDK. Check the local bridge log; no Genetec write was attempted.", checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
            }
            else if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/student-roster", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    payload = ReadGenetecStudentRoster(request.QueryString["externalIdPrefix"], request.QueryString["groupNames"], request.QueryString["limit"]);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new { success = false, errorCode = ex.Code, error = ex.Message, checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Read-only student-roster sync failed: " + ex.GetType().Name);
                    statusCode = 503;
                    payload = new { success = false, errorCode = "STUDENT_ROSTER_SYNC_FAILED", error = "Could not read the configured student-ID scope from Genetec. No pictures or credential values were written.", checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
            }
            else if (String.Equals(request.HttpMethod, "GET", StringComparison.OrdinalIgnoreCase) &&
                String.Equals(path, "/api/genetec/cardholder-photo", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    payload = ReadGenetecCardholderPhoto(request.QueryString["cardholderGuid"]);
                    statusCode = 200;
                }
                catch (BridgeException ex)
                {
                    statusCode = ex.HttpStatusCode;
                    payload = new { success = false, errorCode = ex.Code, error = ex.Message, checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Read-only Cardholder photo sync failed: " + ex.GetType().Name);
                    statusCode = 503;
                    payload = new { success = false, errorCode = "CARDHOLDER_PHOTO_READ_FAILED", error = "Could not read this Cardholder's picture through the installed SDK. No image was saved and no Genetec write was attempted.", checkedAtUtc = DateTime.UtcNow.ToString("o") };
                }
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

                    // Genetec's SDK contains native components. Isolate the native Visitor write in a
                    // short-lived worker so an SDK process crash cannot take down this HTTP bridge.
                    // The worker repeats the request gate, and the parent reports its real result.
                    VisitorWorkerResult workerResult = ExecuteVisitorBookingInWorker(body);
                    payload = workerResult.Payload;
                    statusCode = workerResult.StatusCode;
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
                    Console.Error.WriteLine("Visitor booking sync handler failed: " + ex);
                    Console.Error.Flush();
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
                payload = new { error = "method_not_allowed", writeOperationsEnabled = IsAnyWriteScopeEnabled() };
            }
            else
            {
                statusCode = 404;
                payload = new { error = "not_found", writeOperationsEnabled = IsAnyWriteScopeEnabled() };
            }

            WriteJson(response, statusCode, payload);
        }

        private static bool IsCardholderFaceSyncApproved()
        {
            // This scope only exposes read-only Cardholder photo/credential-reference APIs.
            // The three explicit command-line acknowledgements replace environment-variable toggles.
            // They are not a substitute for actual written school approval and person-specific consent.
            return _enableCardholderFaceSync && _confirmBiometricPolicyApproved &&
                _confirmGenetecCardholderFaceSyncApproved;
        }

        private static void ConfigureStartupScopes(string[] args)
        {
            var allowed = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            {
                "--enable-test-cardholder-writes",
                "--enable-visitor-sync",
                "--enable-visitor-lifecycle-sync",
                "--enable-cardholder-face-sync",
                "--confirm-biometric-policy-approved",
                "--confirm-genetec-cardholder-face-sync-approved"
            };
            var supplied = new HashSet<string>(args ?? new string[0], StringComparer.OrdinalIgnoreCase);
            foreach (string arg in supplied)
            {
                if (!allowed.Contains(arg)) throw new ArgumentException("Unsupported argument '" + arg + "'.");
            }

            _enableTestCardholderWrites = supplied.Contains("--enable-test-cardholder-writes");
            _enableVisitorSync = supplied.Contains("--enable-visitor-sync");
            _enableVisitorLifecycleSync = supplied.Contains("--enable-visitor-lifecycle-sync");
            _enableCardholderFaceSync = supplied.Contains("--enable-cardholder-face-sync");
            _confirmBiometricPolicyApproved = supplied.Contains("--confirm-biometric-policy-approved");
            _confirmGenetecCardholderFaceSyncApproved = supplied.Contains("--confirm-genetec-cardholder-face-sync-approved");

            bool anyFaceOption = _enableCardholderFaceSync || _confirmBiometricPolicyApproved || _confirmGenetecCardholderFaceSyncApproved;
            bool allFaceOptions = _enableCardholderFaceSync && _confirmBiometricPolicyApproved && _confirmGenetecCardholderFaceSyncApproved;
            if (anyFaceOption && !allFaceOptions)
            {
                throw new ArgumentException("Cardholder photo lookup requires all three switches: --enable-cardholder-face-sync, --confirm-biometric-policy-approved, and --confirm-genetec-cardholder-face-sync-approved. Only use these after the required written school approval and person-specific consent are in place.");
            }
        }

        private static void RequireCardholderFaceSyncApproval()
        {
            if (!IsCardholderFaceSyncApproved())
            {
                throw new BridgeException(403, "GENETEC_CARDHOLDER_FACE_SYNC_DISABLED",
                    "Cardholder image/credential-reference sync is disabled. Use the three explicit command-line acknowledgement switches only after written school approval and person-specific consent: --enable-cardholder-face-sync, --confirm-biometric-policy-approved, and --confirm-genetec-cardholder-face-sync-approved.");
            }
        }

        private static object ReadGenetecCardholders(string search, string limitText)
        {
            RequireCardholderFaceSyncApproval();
            search = (search ?? String.Empty).Trim();
            if (search.Length < 2 || search.Length > 80)
                throw new BridgeException(400, "CARDHOLDER_SEARCH_REQUIRED", "Enter at least two characters to search Genetec Cardholders. Broad, unfiltered directory export is not supported.");
            int limit;
            if (!Int32.TryParse(limitText, out limit)) limit = 25;
            if (limit < 1 || limit > 50) limit = 25;

            using (var engine = ConnectToGenetec())
            {
                try
                {
                    // Enumerate through Engine.GetEntities rather than CardholderConfiguration reports.
                    // Some Security Center 5.11 installations return an unsuccessful report result for this report type.
                    // This remains a bounded, read-only query; no Cardholder or credential is modified.
                    var cardholders = engine.GetEntities(EntityType.Cardholder).OfType<Cardholder>().Take(10001).ToList();
                    if (cardholders.Count > 10000)
                        throw new BridgeException(413, "CARDHOLDER_SEARCH_SCOPE_TOO_LARGE", "The directory search exceeded the 10,000-entity safety cap. Narrow the search or use an approved directory filter.");

                    var found = new List<object>();
                    int scanned = 0;
                    foreach (var cardholder in cardholders)
                    {
                        scanned++;
                        string firstName = cardholder.FirstName ?? String.Empty;
                        string lastName = cardholder.LastName ?? String.Empty;
                        string displayName = (firstName + " " + lastName).Trim();
                        if (String.IsNullOrWhiteSpace(displayName)) displayName = cardholder.Name ?? String.Empty;
                        string externalId = GetFirstStringProperty(cardholder, new[] { "StudentId", "ExternalId", "ExternalID", "UniqueId", "UniqueID", "PersonnelId", "EmployeeId" });
                        bool matches = displayName.IndexOf(search, StringComparison.OrdinalIgnoreCase) >= 0 ||
                            (!String.IsNullOrWhiteSpace(externalId) && externalId.IndexOf(search, StringComparison.OrdinalIgnoreCase) >= 0) ||
                            (!String.IsNullOrWhiteSpace(cardholder.Name) && cardholder.Name.IndexOf(search, StringComparison.OrdinalIgnoreCase) >= 0);
                        if (!matches) continue;
                        found.Add(new
                        {
                            cardholderGuid = cardholder.Guid.ToString("D"),
                            displayName = displayName,
                            firstName = firstName,
                            lastName = lastName,
                            externalId = externalId,
                            photoAvailable = HasCardholderPicture(cardholder),
                            credentialReferences = ReadCredentialReferences(cardholder, engine)
                        });
                        if (found.Count >= limit) break;
                    }
                    return new
                    {
                        success = true,
                        mode = "read-only-genetec-cardholder-search",
                        resultsLimited = true,
                        search = search,
                        returnedCount = found.Count,
                        cardholders = found,
                        rawCredentialNumbersReturned = false,
                        imageBytesReturned = false,
                        writeAttempted = false,
                        checkedAtUtc = DateTime.UtcNow.ToString("o")
                    };
                }
                catch (BridgeException) { throw; }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Cardholder enumeration failed: " + ex.GetType().Name + " — " + ex.Message);
                    throw new BridgeException(503, "CARDHOLDER_SEARCH_FAILED", "The read-only Cardholder enumeration failed. Review the local bridge log; no write was attempted.");
                }
                finally { try { engine.LogOff(); } catch { } }
            }
        }

        private static object ReadGenetecStudentGroupCatalog()
        {
            RequireCardholderFaceSyncApproval();
            using (var engine = ConnectToGenetec())
            {
                try
                {
                    // Engine.GetEntities() is a cache enumerator, not a reliable Directory discovery query.
                    // Query the Directory for entity GUIDs, then resolve those entities through the SDK.
                    var cardholders = ReadConfiguredEntities<Cardholder>(engine, EntityType.Cardholder, 10000,
                        "Cardholder", "CARDHOLDER_GROUP_SCAN_TOO_LARGE");
                    var groups = ReadConfiguredEntities<CardholderGroup>(engine, EntityType.CardholderGroup, 5000,
                        "Cardholder group", "CARDHOLDER_GROUP_SCAN_TOO_LARGE");
                    if (groups.Count == 0)
                        throw new BridgeException(503, "CARDHOLDER_GROUPS_NOT_FOUND",
                            "The Directory EntityConfiguration query returned no CardholderGroup entities. Verify the SDK account permissions and group query result.");

                    var membershipIndex = ReadCardholderGroupMembershipIndex(cardholders, groups, true);
                    var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
                    foreach (var group in groups)
                    {
                        string groupName = GetFirstStringProperty(group, new[] { "Name", "DisplayName", "GroupName", "EntityName" });
                        if (!String.IsNullOrWhiteSpace(groupName) && !counts.ContainsKey(groupName)) counts[groupName] = 0;
                    }
                    int scanned = 0;
                    foreach (var cardholder in cardholders)
                    {
                        scanned++;
                        foreach (string groupName in ReadCardholderGroupNames(cardholder, engine, membershipIndex))
                        {
                            if (counts.ContainsKey(groupName)) counts[groupName]++;
                        }
                    }
                    var groupsResult = counts.OrderBy(kv => kv.Key, StringComparer.OrdinalIgnoreCase)
                        .Select(kv => new { name = kv.Key, cardholderCount = kv.Value }).ToList();
                    return new
                    {
                        success = true,
                        mode = "read-only-genetec-cardholder-group-catalog",
                        groups = groupsResult,
                        groupCount = groupsResult.Count,
                        scannedCardholderCount = scanned,
                        membershipMapping = "Cardholder.IsMemberOf(CardholderGroup)",
                        personallyIdentifyingCardholderRecordsReturned = false,
                        portraitsReturned = false,
                        rawCredentialNumbersReturned = false,
                        writeAttempted = false,
                        checkedAtUtc = DateTime.UtcNow.ToString("o")
                    };
                }
                catch (BridgeException) { throw; }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Cardholder group catalog failed: " + ex.GetType().Name + " — " + ex.Message);
                    throw new BridgeException(503, "CARDHOLDER_GROUP_CATALOG_FAILED", "Could not enumerate Cardholders or resolve Cardholder.IsMemberOf group membership through the installed SDK. Review the local bridge log; no write was attempted.");
                }
                finally { try { engine.LogOff(); } catch { } }
            }
        }

        private static List<T> ReadConfiguredEntities<T>(Engine engine, EntityType entityType, int maxRecords,
            string displayName, string tooLargeCode) where T : class
        {
            var query = engine.ReportManager.CreateReportQuery(ReportType.EntityConfiguration) as EntityConfigurationQuery;
            if (query == null)
                throw new BridgeException(503, "ENTITY_CONFIGURATION_QUERY_UNAVAILABLE",
                    "The SDK did not create an EntityConfigurationQuery for " + displayName + ".");
            query.EntityTypeFilter.Add(entityType);
            var result = query.Query();
            if (result == null || !result.Success || result.Data == null)
                throw new BridgeException(503, "ENTITY_CONFIGURATION_QUERY_FAILED",
                    "The Directory EntityConfiguration query failed for " + displayName + ". Check the bridge log; no write was attempted.");
            if (result.Data.Rows.Count > maxRecords)
                throw new BridgeException(413, tooLargeCode,
                    "The Directory returned more than " + maxRecords.ToString(CultureInfo.InvariantCulture) + " " + displayName + " records. Narrow the approved roster scope.");

            var guids = new List<Guid>();
            foreach (DataRow row in result.Data.Rows)
            {
                if (row.ItemArray.Length == 0) continue;
                Guid guid;
                object value = row[0];
                if (value is Guid) guid = (Guid)value;
                else if (value == null || !Guid.TryParse(Convert.ToString(value, CultureInfo.InvariantCulture), out guid)) continue;
                if (guid != Guid.Empty && !guids.Contains(guid)) guids.Add(guid);
            }
            if (result.Data.Rows.Count > 0 && guids.Count == 0)
                throw new BridgeException(503, "ENTITY_CONFIGURATION_RESULT_SHAPE_UNSUPPORTED",
                    "The Directory query returned rows but no entity GUIDs in the expected first column for " + displayName + ".");

            var entities = new List<T>();
            foreach (Guid guid in guids)
            {
                object entity;
                try { entity = engine.GetEntity(guid); }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Could not load " + displayName + " entity " + guid.ToString("D") + ": " + ex.GetType().Name + " — " + ex.Message);
                    throw new BridgeException(503, "ENTITY_CONFIGURATION_ENTITY_LOAD_FAILED",
                        "The Directory returned a " + displayName + " GUID that the SDK could not load. Check the bridge log.");
                }
                T typed = entity as T;
                if (typed == null)
                    throw new BridgeException(503, "ENTITY_CONFIGURATION_ENTITY_TYPE_MISMATCH",
                        "The Directory query returned an entity that could not be loaded as " + displayName + ".");
                entities.Add(typed);
            }
            if (guids.Count != entities.Count)
                throw new BridgeException(503, "ENTITY_CONFIGURATION_ENTITY_LOAD_INCOMPLETE",
                    "Not every queried " + displayName + " could be loaded. Refusing to use a partial roster.");
            return entities;
        }

        private static object ReadGenetecStudentRoster(string externalIdPrefix, string groupNamesText, string limitText)
        {
            RequireCardholderFaceSyncApproval();
            externalIdPrefix = (externalIdPrefix ?? String.Empty).Trim();
            if (!String.IsNullOrWhiteSpace(externalIdPrefix) && !Regex.IsMatch(externalIdPrefix, @"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$"))
                throw new BridgeException(400, "STUDENT_ID_PREFIX_INVALID", "External ID prefix may contain only letters, digits, dot, underscore and hyphen (maximum 32 characters).");
            var selectedGroups = ParseStudentGroupNames(groupNamesText);
            if (selectedGroups.Count == 0 && String.IsNullOrWhiteSpace(externalIdPrefix))
                throw new BridgeException(400, "STUDENT_SCOPE_REQUIRED", "Select one or more approved student-only Cardholder groups, or provide a verified student External ID prefix. Unfiltered Cardholder export is disabled.");
            int limit;
            if (!Int32.TryParse(limitText, out limit)) limit = 1000;
            if (limit < 1 || limit > 1000)
                throw new BridgeException(400, "STUDENT_ROSTER_LIMIT_INVALID", "Roster import limit must be between 1 and 1000.");

            using (var engine = ConnectToGenetec())
            {
                try
                {
                    // Discover the full Directory roster through EntityConfiguration; GetEntities() only
                    // enumerates entities already in the SDK cache and can omit Cardholders/groups.
                    var cardholders = ReadConfiguredEntities<Cardholder>(engine, EntityType.Cardholder, 10000,
                        "Cardholder", "STUDENT_ROSTER_SCOPE_TOO_LARGE");
                    var membershipIndex = new Dictionary<Guid, List<string>>();
                    if (selectedGroups.Count > 0)
                    {
                        var groups = ReadConfiguredEntities<CardholderGroup>(engine, EntityType.CardholderGroup, 5000,
                            "Cardholder group", "STUDENT_ROSTER_SCOPE_TOO_LARGE");
                        membershipIndex = ReadCardholderGroupMembershipIndex(cardholders, groups, true);
                    }
                    var found = new List<object>();
                    int scanned = 0;
                    int matched = 0;
                    foreach (var cardholder in cardholders)
                    {
                        scanned++;
                        string externalId = GetFirstStringProperty(cardholder, new[] { "StudentId", "ExternalId", "ExternalID", "UniqueId", "UniqueID", "PersonnelId", "EmployeeId", "EmployeeNumber" });
                        if (!String.IsNullOrWhiteSpace(externalId)) externalId = externalId.Trim();
                        if (!String.IsNullOrWhiteSpace(externalIdPrefix) && (String.IsNullOrWhiteSpace(externalId) || !externalId.StartsWith(externalIdPrefix, StringComparison.OrdinalIgnoreCase))) continue;
                        var cardholderGroups = ReadCardholderGroupNames(cardholder, engine, membershipIndex);
                        if (selectedGroups.Count > 0 && !cardholderGroups.Any(g => selectedGroups.Contains(g, StringComparer.OrdinalIgnoreCase))) continue;
                        matched++;
                        if (found.Count >= limit) continue;
                        string firstName = cardholder.FirstName ?? String.Empty;
                        string lastName = cardholder.LastName ?? String.Empty;
                        string displayName = (firstName + " " + lastName).Trim();
                        if (String.IsNullOrWhiteSpace(displayName)) displayName = cardholder.Name ?? String.Empty;
                        found.Add(new
                        {
                            cardholderGuid = cardholder.Guid.ToString("D"),
                            displayName = displayName,
                            firstName = firstName,
                            lastName = lastName,
                            externalId = externalId,
                            cardholderGroups = cardholderGroups,
                            photoAvailable = HasCardholderPicture(cardholder),
                            credentialReferences = ReadCredentialReferences(cardholder, engine)
                        });
                    }
                    return new
                    {
                        success = true,
                        mode = selectedGroups.Count > 0 ? "read-only-genetec-student-roster-group-import" : "read-only-genetec-student-roster-prefix-import",
                        externalIdPrefix = externalIdPrefix,
                        groupNames = selectedGroups,
                        scannedCount = scanned,
                        matchedCount = matched,
                        returnedCount = found.Count,
                        resultsLimited = matched > found.Count,
                        cardholders = found,
                        rawCredentialNumbersReturned = false,
                        imageBytesReturned = false,
                        writeAttempted = false,
                        checkedAtUtc = DateTime.UtcNow.ToString("o")
                    };
                }
                catch (BridgeException) { throw; }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Student-roster entity enumeration failed: " + ex.GetType().Name + " — " + ex.Message);
                    throw new BridgeException(503, "STUDENT_ROSTER_SYNC_FAILED", "The read-only student-roster enumeration failed. Review the local bridge log; no write was attempted.");
                }
                finally { try { engine.LogOff(); } catch { } }
            }
        }

        private static List<string> ParseStudentGroupNames(string value)
        {
            var names = new List<string>();
            if (String.IsNullOrWhiteSpace(value)) return names;
            foreach (string raw in value.Split(new[] { '|' }, StringSplitOptions.RemoveEmptyEntries))
            {
                string name = (raw ?? String.Empty).Trim();
                if (name.Length == 0 || name.Length > 100 || name.Any(Char.IsControl) || name.Contains("|"))
                    throw new BridgeException(400, "STUDENT_GROUP_FILTER_INVALID", "Each selected Genetec Cardholder group must be a valid name of at most 100 characters.");
                if (!names.Contains(name, StringComparer.OrdinalIgnoreCase)) names.Add(name);
                if (names.Count > 20)
                    throw new BridgeException(400, "STUDENT_GROUP_FILTER_LIMIT", "Select no more than 20 approved student Cardholder groups at once.");
            }
            return names;
        }

        private static Dictionary<Guid, List<string>> ReadCardholderGroupMembershipIndex(
            IList<Cardholder> cardholders, IList<CardholderGroup> groups, bool requireReliableMembership)
        {
            var index = new Dictionary<Guid, List<string>>();
            if (groups == null || groups.Count == 0)
            {
                if (requireReliableMembership)
                    throw new BridgeException(503, "CARDHOLDER_GROUPS_NOT_FOUND",
                        "No CardholderGroup entities were returned by the Directory query. Refusing group-scoped import.");
                return index;
            }

            // Security Center exposes membership as Cardholder.IsMemberOf(CardholderGroup), including
            // membership inherited through nested groups. It is a method, not a public collection property.
            // Use reflection so the bridge still compiles against SDK builds that do not expose that method.
            MethodInfo isMemberOf = typeof(Cardholder).GetMethods(BindingFlags.Instance | BindingFlags.Public)
                .FirstOrDefault(method =>
                {
                    if (!String.Equals(method.Name, "IsMemberOf", StringComparison.Ordinal) || method.ReturnType != typeof(bool)) return false;
                    var parameters = method.GetParameters();
                    return parameters.Length == 1 && parameters[0].ParameterType == typeof(CardholderGroup);
                });

            if (isMemberOf != null)
            {
                foreach (var cardholder in cardholders)
                {
                    foreach (var group in groups)
                    {
                        bool member;
                        try
                        {
                            object result = isMemberOf.Invoke(cardholder, new object[] { group });
                            if (!(result is bool))
                                throw new InvalidOperationException("Cardholder.IsMemberOf returned a non-Boolean result.");
                            member = (bool)result;
                        }
                        catch (Exception ex)
                        {
                            Exception detail = ex is TargetInvocationException && ex.InnerException != null ? ex.InnerException : ex;
                            Console.Error.WriteLine("Cardholder.IsMemberOf failed for group " + group.Guid.ToString("D") +
                                ", cardholder " + cardholder.Guid.ToString("D") + ": " + detail.GetType().Name + " — " + detail.Message);
                            throw new BridgeException(503, "CARDHOLDER_GROUP_MEMBERSHIP_CHECK_FAILED",
                                "The SDK exposed Cardholder.IsMemberOf but failed while evaluating a membership. Refusing a partial group catalog; inspect the local bridge log.");
                        }
                        if (!member) continue;
                        string groupName = GetFirstStringProperty(group, new[] { "Name", "DisplayName", "GroupName", "EntityName" });
                        if (String.IsNullOrWhiteSpace(groupName)) continue;
                        if (!index.ContainsKey(cardholder.Guid)) index[cardholder.Guid] = new List<string>();
                        if (!index[cardholder.Guid].Contains(groupName, StringComparer.OrdinalIgnoreCase)) index[cardholder.Guid].Add(groupName);
                    }
                }
                if (requireReliableMembership && cardholders.Count > 0 && index.Count == 0)
                    throw new BridgeException(503, "CARDHOLDER_GROUP_MEMBERSHIP_EMPTY",
                        "The SDK membership API ran successfully but found no Cardholder-to-group relationships. Confirm that the Directory query includes the expected student groups and Cardholders.");
                Console.WriteLine("Cardholder group membership resolved with Cardholder.IsMemberOf(CardholderGroup); cardholders=" +
                    cardholders.Count.ToString(CultureInfo.InvariantCulture) + ", groups=" + groups.Count.ToString(CultureInfo.InvariantCulture) +
                    ", linkedCardholders=" + index.Count.ToString(CultureInfo.InvariantCulture));
                return index;
            }

            // Older SDK fallback: inspect known member collections on queried group entities.
            foreach (object group in groups)
            {
                string groupName = GetFirstStringProperty(group, new[] { "Name", "DisplayName", "GroupName", "EntityName" });
                if (String.IsNullOrWhiteSpace(groupName)) continue;
                foreach (string propertyName in new[] { "Cardholders", "Members", "MemberCardholders", "CardholderMembers", "CardholderIds", "MemberIds", "Entities" })
                {
                    object rawMembers = GetPropertyValue(group, propertyName);
                    var members = rawMembers as System.Collections.IEnumerable;
                    if (members == null || rawMembers is string) continue;
                    foreach (object member in members)
                    {
                        Guid memberGuid;
                        if (!TryReadEntityGuid(member, out memberGuid)) continue;
                        if (!index.ContainsKey(memberGuid)) index[memberGuid] = new List<string>();
                        if (!index[memberGuid].Contains(groupName, StringComparer.OrdinalIgnoreCase)) index[memberGuid].Add(groupName);
                    }
                }
            }
            if (index.Count == 0 && requireReliableMembership)
            {
                Console.Error.WriteLine("Group membership mapping unavailable. Cardholder SDK members: " +
                    String.Join(", ", typeof(Cardholder).GetMethods(BindingFlags.Instance | BindingFlags.Public).Select(m => m.Name).Distinct().OrderBy(n => n, StringComparer.OrdinalIgnoreCase)));
                throw new BridgeException(503, "CARDHOLDER_GROUP_MEMBERSHIP_API_UNAVAILABLE",
                    "This installed Genetec SDK exposes neither Cardholder.IsMemberOf(CardholderGroup) nor readable group member collections. Do not run an unfiltered import. Check bridge log and SDK compatibility.");
            }
            return index;
        }

        private static bool TryReadEntityGuid(object value, out Guid guid)
        {
            guid = Guid.Empty;
            if (value == null) return false;
            if (value is Guid) { guid = (Guid)value; return true; }
            if (value is string && Guid.TryParse((string)value, out guid)) return true;
            foreach (string propertyName in new[] { "Guid", "EntityGuid", "CardholderGuid", "Id", "EntityId" })
            {
                object raw = GetPropertyValue(value, propertyName);
                if (raw is Guid) { guid = (Guid)raw; return true; }
                if (raw != null && Guid.TryParse(Convert.ToString(raw, CultureInfo.InvariantCulture), out guid)) return true;
            }
            return false;
        }

        private static List<string> ReadCardholderGroupNames(Cardholder cardholder, Engine engine, IDictionary<Guid, List<string>> membershipIndex)
        {
            var names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (string propertyName in new[] { "CardholderGroups", "CardholderGroupMemberships", "GroupMemberships", "CardholderGroupIds", "Groups", "GroupIds" })
            {
                AddCardholderGroupNames(GetPropertyValue(cardholder, propertyName), engine, names, 0);
            }
            if (membershipIndex != null && membershipIndex.TryGetValue(cardholder.Guid, out var indexed))
                foreach (string name in indexed) if (!String.IsNullOrWhiteSpace(name)) names.Add(name.Trim());
            return names.Where(n => !String.IsNullOrWhiteSpace(n)).OrderBy(n => n, StringComparer.OrdinalIgnoreCase).Take(30).ToList();
        }

        private static void AddCardholderGroupNames(object value, Engine engine, HashSet<string> names, int depth)
        {
            if (value == null || depth > 3) return;
            if (value is Guid || (value is string && Guid.TryParse((string)value, out _)))
            {
                Guid id;
                if (TryReadEntityGuid(value, out id))
                {
                    object entity = null;
                    try { entity = engine.GetEntity(id); } catch { }
                    string entityName = GetFirstStringProperty(entity, new[] { "Name", "DisplayName", "GroupName", "EntityName" });
                    if (!String.IsNullOrWhiteSpace(entityName)) names.Add(entityName.Trim());
                }
                return;
            }
            if (value is string)
            {
                string text = ((string)value).Trim();
                if (text.Length > 0 && text.Length <= 100 && !text.Any(Char.IsControl)) names.Add(text);
                return;
            }
            var enumerable = value as System.Collections.IEnumerable;
            if (enumerable != null && !(value is byte[]))
            {
                foreach (object item in enumerable) AddCardholderGroupNames(item, engine, names, depth + 1);
                return;
            }
            string name = GetFirstStringProperty(value, new[] { "Name", "DisplayName", "GroupName", "CardholderGroupName", "EntityName" });
            if (!String.IsNullOrWhiteSpace(name)) { names.Add(name.Trim()); return; }
            object nested = GetPropertyValue(value, "Guid") ?? GetPropertyValue(value, "EntityGuid") ?? GetPropertyValue(value, "Id");
            if (nested != null) AddCardholderGroupNames(nested, engine, names, depth + 1);
        }

        private static object ReadGenetecCardholderPhoto(string cardholderGuidText)
        {
            RequireCardholderFaceSyncApproval();
            Guid cardholderGuid;
            if (!Guid.TryParse(cardholderGuidText, out cardholderGuid))
                throw new BridgeException(400, "INVALID_CARDHOLDER_GUID", "cardholderGuid must be the GUID returned by the Genetec Cardholder search.");

            using (var engine = ConnectToGenetec())
            {
                try
                {
                    var cardholder = engine.GetEntity(cardholderGuid) as Cardholder;
                    if (cardholder == null)
                        throw new BridgeException(404, "CARDHOLDER_NOT_FOUND", "The selected Genetec Cardholder no longer exists or is not readable.");
                    byte[] imageBytes = GetCardholderPictureBytes(cardholder);
                    if (imageBytes == null || imageBytes.Length == 0)
                        throw new BridgeException(422, "CARDHOLDER_PHOTO_UNAVAILABLE", "The selected Genetec Cardholder has no picture readable through the installed SDK. Import cannot continue without an individually assigned portrait.");
                    if (imageBytes.Length > 1800000)
                        throw new BridgeException(413, "CARDHOLDER_PHOTO_TOO_LARGE", "The selected Cardholder picture is larger than the 1.8 MB local import limit.");
                    string mime = DetectImageMime(imageBytes);
                    if (mime == null)
                        throw new BridgeException(415, "CARDHOLDER_PHOTO_FORMAT_UNSUPPORTED", "The SDK returned a picture in an unsupported format. Use a JPEG or PNG picture in Genetec.");

                    string firstName = cardholder.FirstName ?? String.Empty;
                    string lastName = cardholder.LastName ?? String.Empty;
                    string displayName = (firstName + " " + lastName).Trim();
                    if (String.IsNullOrWhiteSpace(displayName)) displayName = cardholder.Name ?? String.Empty;
                    return new
                    {
                        success = true,
                        mode = "read-only-genetec-cardholder-photo",
                        cardholder = new
                        {
                            cardholderGuid = cardholder.Guid.ToString("D"),
                            displayName = displayName,
                            firstName = firstName,
                            lastName = lastName,
                            externalId = GetFirstStringProperty(cardholder, new[] { "StudentId", "ExternalId", "ExternalID", "UniqueId", "UniqueID", "PersonnelId", "EmployeeId" }),
                            cardholderGroups = ReadCardholderGroupNames(cardholder, engine, null),
                            credentialReferences = ReadCredentialReferences(cardholder, engine)
                        },
                        imageMimeType = mime,
                        imageBase64 = Convert.ToBase64String(imageBytes),
                        rawCredentialNumbersReturned = false,
                        persistedByBridge = false,
                        writeAttempted = false,
                        checkedAtUtc = DateTime.UtcNow.ToString("o")
                    };
                }
                catch (BridgeException) { throw; }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Cardholder photo query failed: " + ex.GetType().Name);
                    throw new BridgeException(503, "CARDHOLDER_PHOTO_READ_FAILED", "Could not read the Cardholder picture through this installed SDK build. Inspect the local bridge log; no Genetec write was attempted.");
                }
                finally { try { engine.LogOff(); } catch { } }
            }
        }

        private static string GetFirstStringProperty(object target, IEnumerable<string> names)
        {
            foreach (string name in names)
            {
                string value = GetStringProperty(target, name);
                if (!String.IsNullOrWhiteSpace(value) && value.Length <= 160) return value.Trim();
            }
            return null;
        }

        private static object GetPropertyValue(object target, string name)
        {
            if (target == null) return null;
            try
            {
                var property = target.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.FlattenHierarchy);
                return property == null || property.GetIndexParameters().Length != 0 ? null : property.GetValue(target, null);
            }
            catch { return null; }
        }

        private static object GetCardholderPictureObject(Cardholder cardholder)
        {
            foreach (string name in new[] { "Picture", "Photo", "Image", "PictureData", "PhotoData", "ImageData" })
            {
                object value = GetPropertyValue(cardholder, name);
                if (value != null) return value;
            }
            foreach (string methodName in new[] { "GetPicture", "GetPhoto", "GetPictureData", "GetPhotoData", "GetImage" })
            {
                try
                {
                    MethodInfo method = cardholder.GetType().GetMethod(methodName, BindingFlags.Instance | BindingFlags.Public, null, Type.EmptyTypes, null);
                    if (method == null) continue;
                    object value = method.Invoke(cardholder, null);
                    if (value != null) return value;
                }
                catch { }
            }
            return null;
        }

        private static bool HasCardholderPicture(Cardholder cardholder)
        {
            object value = GetCardholderPictureObject(cardholder);
            if (value is byte[]) return ((byte[])value).Length > 0;
            if (value is string) return ((string)value).Length > 100;
            return value != null;
        }

        private static byte[] GetCardholderPictureBytes(Cardholder cardholder)
        {
            return ConvertPictureValue(GetCardholderPictureObject(cardholder), 0);
        }

        private static byte[] ConvertPictureValue(object value, int depth)
        {
            if (value == null || depth > 3) return null;
            if (value is byte[])
            {
                var bytes = (byte[])value;
                return bytes.Length <= 1800000 ? bytes : null;
            }
            if (value is System.Drawing.Image)
            {
                using (var ms = new MemoryStream())
                {
                    ((System.Drawing.Image)value).Save(ms, ImageFormat.Jpeg);
                    return ms.Length <= 1800000 ? ms.ToArray() : null;
                }
            }
            if (value is System.IO.Stream)
            {
                var stream = (System.IO.Stream)value;
                try { if (stream.CanSeek) stream.Position = 0; } catch { }
                using (var ms = new MemoryStream())
                {
                    var buffer = new byte[8192]; int read;
                    while ((read = stream.Read(buffer, 0, buffer.Length)) > 0)
                    {
                        if (ms.Length + read > 1800000) return null;
                        ms.Write(buffer, 0, read);
                    }
                    return ms.ToArray();
                }
            }
            if (value is string)
            {
                string text = ((string)value).Trim();
                int comma = text.IndexOf("base64,", StringComparison.OrdinalIgnoreCase);
                if (comma >= 0) text = text.Substring(comma + 7);
                if (text.Length < 100 || text.Length > 2500000) return null;
                try { var bytes = Convert.FromBase64String(text); return bytes.Length <= 1800000 ? bytes : null; }
                catch { return null; }
            }
            foreach (string methodName in new[] { "ToByteArray", "GetBytes", "GetImageBytes", "ToBitmap", "GetBitmap", "GetImage" })
            {
                try
                {
                    MethodInfo method = value.GetType().GetMethod(methodName, BindingFlags.Instance | BindingFlags.Public, null, Type.EmptyTypes, null);
                    if (method == null) continue;
                    object nested = method.Invoke(value, null);
                    if (nested == null || Object.ReferenceEquals(nested, value)) continue;
                    var bytes = ConvertPictureValue(nested, depth + 1);
                    if (bytes != null) return bytes;
                }
                catch { }
            }
            foreach (string propertyName in new[] { "Data", "Bytes", "ImageData", "PictureData", "Content", "Buffer", "Bitmap", "Image", "Picture", "Value" })
            {
                object nested = GetPropertyValue(value, propertyName);
                if (nested == null || Object.ReferenceEquals(nested, value)) continue;
                var bytes = ConvertPictureValue(nested, depth + 1);
                if (bytes != null) return bytes;
            }
            return null;
        }

        private static string DetectImageMime(byte[] bytes)
        {
            if (bytes == null || bytes.Length < 12) return null;
            if (bytes[0] == 0xFF && bytes[1] == 0xD8 && bytes[2] == 0xFF) return "image/jpeg";
            if (bytes[0] == 0x89 && bytes[1] == 0x50 && bytes[2] == 0x4E && bytes[3] == 0x47) return "image/png";
            if (bytes[0] == 0x52 && bytes[1] == 0x49 && bytes[2] == 0x46 && bytes[3] == 0x46 && bytes[8] == 0x57 && bytes[9] == 0x45 && bytes[10] == 0x42 && bytes[11] == 0x50) return "image/webp";
            return null;
        }

        private static object ReadCredentialReferences(Cardholder cardholder, Engine engine)
        {
            var result = new List<object>();
            object value = GetPropertyValue(cardholder, "Credentials");
            var enumerable = value as System.Collections.IEnumerable;
            if (enumerable == null) return result;
            foreach (object item in enumerable)
            {
                if (item == null || result.Count >= 20) continue;
                object credential = item;
                if (item is Guid) credential = engine.GetEntity((Guid)item) ?? item;
                string guid = null;
                object guidValue = GetPropertyValue(credential, "Guid");
                if (guidValue is Guid) guid = ((Guid)guidValue).ToString("D");
                else if (guidValue != null && Guid.TryParse(Convert.ToString(guidValue, CultureInfo.InvariantCulture), out Guid parsed)) guid = parsed.ToString("D");
                if (String.IsNullOrWhiteSpace(guid) && item is Guid) guid = ((Guid)item).ToString("D");
                string name = GetFirstStringProperty(credential, new[] { "CredentialType", "TypeName", "Type" }) ?? "Assigned credential";
                string rawIdentifier = GetFirstStringProperty(credential, new[] { "CredentialNumber", "CardNumber", "Number", "Code", "Identifier", "Value" });
                string masked = MaskCredentialIdentifier(rawIdentifier);
                object activeValue = GetPropertyValue(credential, "IsActive") ?? GetPropertyValue(credential, "Active");
                bool? active = null;
                if (activeValue is bool) active = (bool)activeValue;
                result.Add(new { guid = guid, name = name, maskedIdentifier = masked, active = active, rawNumberReturned = false });
            }
            return result;
        }

        private static string MaskCredentialIdentifier(string value)
        {
            if (String.IsNullOrWhiteSpace(value)) return null;
            string normalized = new string(value.Where(Char.IsLetterOrDigit).ToArray());
            if (normalized.Length == 0) return null;
            string suffix = normalized.Substring(Math.Max(0, normalized.Length - 4));
            return "••••" + suffix;
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
                        writeOperationsEnabled = IsAnyWriteScopeEnabled(),
                        writeScope = BuildEnabledWriteScopeDescription(),
                        visitorManagementLicensed = visitorManagementLicensed,
                        visitorSyncEnabled = IsVisitorSyncEnabled() && visitorManagementLicensed,
                        visitorSyncScope = IsVisitorSyncEnabled() && visitorManagementLicensed
                            ? "native-visitor-create-update-for-invited-synthetic-booking-only" : "disabled",
                        visitorLifecycleSyncEnabled = IsVisitorLifecycleSyncEnabled() && visitorManagementLicensed,
                        cardholderFaceSyncEnabled = IsCardholderFaceSyncApproved(),
                        cardholderFaceSyncScope = IsCardholderFaceSyncApproved() ? "read-only-approved-cardholder-image-and-masked-credential-reference" : "disabled"
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

        private static VisitorWorkerResult ExecuteVisitorBookingInWorker(VisitorSyncRequest request)
        {
            string executablePath = Assembly.GetExecutingAssembly().Location;
            if (String.IsNullOrWhiteSpace(executablePath) || !System.IO.File.Exists(executablePath))
                throw new BridgeException(503, "VISITOR_SYNC_WORKER_UNAVAILABLE", "The bridge executable path could not be resolved for the isolated Visitor sync worker.");

            var arguments = new List<string> { "--visitor-booking-worker" };
            if (IsTestWriteEnabled()) arguments.Add("--enable-test-cardholder-writes");
            if (IsVisitorSyncEnabled()) arguments.Add("--enable-visitor-sync");
            if (IsVisitorLifecycleSyncEnabled()) arguments.Add("--enable-visitor-lifecycle-sync");
            if (_enableCardholderFaceSync) arguments.Add("--enable-cardholder-face-sync");
            if (_confirmBiometricPolicyApproved) arguments.Add("--confirm-biometric-policy-approved");
            if (_confirmGenetecCardholderFaceSyncApproved) arguments.Add("--confirm-genetec-cardholder-face-sync-approved");

            var startInfo = new ProcessStartInfo
            {
                FileName = executablePath,
                Arguments = String.Join(" ", arguments),
                WorkingDirectory = AppDomain.CurrentDomain.BaseDirectory,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true
            };

            string requestJson = Serializer.Serialize(request);
            using (var process = Process.Start(startInfo))
            {
                if (process == null)
                    throw new BridgeException(503, "VISITOR_SYNC_WORKER_START_FAILED", "The isolated Genetec Visitor sync worker could not be started.");

                Task<string> stdoutTask = process.StandardOutput.ReadToEndAsync();
                Task<string> stderrTask = process.StandardError.ReadToEndAsync();
                process.StandardInput.Write(requestJson);
                process.StandardInput.Close();

                if (!process.WaitForExit(120000))
                {
                    try { process.Kill(); } catch { }
                    try { process.WaitForExit(5000); } catch { }
                    Console.Error.WriteLine("Visitor sync worker timed out after 120 seconds. booking=" + request.BookingId + "; outcome is unknown; perform read-only lookup before retrying.");
                    Console.Error.Flush();
                    throw new BridgeException(504, "VISITOR_SYNC_WORKER_TIMEOUT", "The isolated Genetec Visitor sync did not finish within 120 seconds. Its outcome may be unknown; perform a read-only Visitor lookup before retrying.");
                }

                Task.WaitAll(stdoutTask, stderrTask);
                string stdout = (stdoutTask.Result ?? String.Empty).Trim();
                string stderr = stderrTask.Result ?? String.Empty;
                if (!String.IsNullOrWhiteSpace(stderr))
                {
                    // The worker's stderr contains SDK stage diagnostics/stack details, but not visitor
                    // names, credentials, image bytes, or request bodies.
                    Console.Error.WriteLine("Visitor sync worker diagnostics (booking=" + request.BookingId + "): " +
                        (stderr.Length > 16000 ? stderr.Substring(stderr.Length - 16000) : stderr));
                    Console.Error.Flush();
                }

                if (stdout.Length == 0 || stdout.Length > 1024 * 1024)
                {
                    Console.Error.WriteLine("Visitor sync worker exited with code " + process.ExitCode +
                        " and no valid result envelope; booking=" + request.BookingId + ". Inspect Genetec before retrying.");
                    Console.Error.Flush();
                    throw new BridgeException(503, "VISITOR_SYNC_WORKER_CRASHED", "The native Genetec Visitor sync worker exited without returning a result. The operation may have committed; perform a read-only Visitor lookup before retrying.");
                }

                Dictionary<string, object> envelope;
                try { envelope = Serializer.DeserializeObject(stdout) as Dictionary<string, object>; }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Visitor sync worker returned invalid JSON: " + ex);
                    throw new BridgeException(503, "VISITOR_SYNC_WORKER_INVALID_RESULT", "The isolated Genetec Visitor sync worker returned an unreadable result. Perform a read-only Visitor lookup before retrying.");
                }

                object rawStatus;
                object rawPayload;
                int statusCode;
                if (envelope == null || !envelope.TryGetValue("statusCode", out rawStatus) ||
                    !Int32.TryParse(Convert.ToString(rawStatus, CultureInfo.InvariantCulture), out statusCode) ||
                    !envelope.TryGetValue("payload", out rawPayload) || rawPayload == null)
                {
                    throw new BridgeException(503, "VISITOR_SYNC_WORKER_INVALID_RESULT", "The isolated Genetec Visitor sync worker did not return a recognised result. Perform a read-only Visitor lookup before retrying.");
                }

                return new VisitorWorkerResult { StatusCode = statusCode, Payload = rawPayload };
            }
        }

        private static int RunVisitorBookingWorker(string[] args)
        {
            TextWriter stdout = Console.Out;
            try
            {
                ConfigureStartupScopes(args);
                if (!IsVisitorSyncEnabled())
                    throw new BridgeException(403, "VISITOR_SYNC_DISABLED", "The isolated worker did not receive the explicit visitor-sync scope.");

                _sdkDirectory = Environment.GetEnvironmentVariable("GSC_SDK");
                if (String.IsNullOrWhiteSpace(_sdkDirectory))
                    _sdkDirectory = @"C:\Program Files (x86)\Genetec Security Center 5.11 SDK";
                AppDomain.CurrentDomain.AssemblyResolve += ResolveSdkAssembly;

                string body;
                using (var reader = new StreamReader(Console.OpenStandardInput(), Encoding.UTF8))
                    body = reader.ReadToEnd();
                if (String.IsNullOrWhiteSpace(body) || body.Length > 8192)
                    throw new BridgeException(400, "INVALID_WORKER_REQUEST", "The isolated Visitor worker request is empty or too large.");

                VisitorSyncRequest request;
                try { request = Serializer.Deserialize<VisitorSyncRequest>(body); }
                catch (Exception ex)
                {
                    Console.Error.WriteLine("Visitor worker JSON parse failed: " + ex);
                    throw new BridgeException(400, "INVALID_JSON", "The isolated Visitor worker request was not valid JSON.");
                }
                if (request == null || !String.Equals(request.Confirmation, VisitorSyncConfirmation, StringComparison.Ordinal))
                    throw new BridgeException(400, "VISITOR_SYNC_CONFIRMATION_REQUIRED", "The isolated worker requires the explicit visitor-sync confirmation.");

                // Reserve stdout exclusively for one machine-readable result envelope. SDK and stage
                // diagnostics go to stderr so they cannot corrupt the parent/worker protocol.
                Console.SetOut(Console.Error);
                int statusCode = 200;
                object payload;
                try
                {
                    payload = CreateOrUpdateVmsVisitor(request);
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
                    Console.Error.WriteLine("Unhandled Visitor worker exception: " + ex);
                    statusCode = 503;
                    payload = new
                    {
                        success = false,
                        mode = "guarded-visitor-sync",
                        errorCode = "VISITOR_SYNC_WORKER_UNHANDLED_FAILURE",
                        error = "The isolated Visitor sync worker failed unexpectedly. Check its diagnostics and perform a read-only Visitor lookup before retrying.",
                        checkedAtUtc = DateTime.UtcNow.ToString("o"),
                        visitorSyncEnabled = IsVisitorSyncEnabled()
                    };
                }

                Console.SetOut(stdout);
                Console.WriteLine(Serializer.Serialize(new { statusCode = statusCode, payload = payload }));
                Console.Out.Flush();
                return 0;
            }
            catch (BridgeException ex)
            {
                try { Console.SetOut(stdout); } catch { }
                Console.WriteLine(Serializer.Serialize(new
                {
                    statusCode = ex.HttpStatusCode,
                    payload = new { success = false, mode = "guarded-visitor-sync-worker", errorCode = ex.Code, error = ex.Message }
                }));
                Console.Out.Flush();
                return 0;
            }
            catch (Exception ex)
            {
                try { Console.SetOut(Console.Error); } catch { }
                Console.Error.WriteLine("Visitor worker bootstrap failed: " + ex);
                Console.Error.Flush();
                try { Console.SetOut(stdout); } catch { }
                Console.WriteLine(Serializer.Serialize(new
                {
                    statusCode = 503,
                    payload = new { success = false, mode = "guarded-visitor-sync-worker", errorCode = "VISITOR_SYNC_WORKER_BOOTSTRAP_FAILED", error = "The isolated Visitor sync worker could not initialise. Check the local bridge log." }
                }));
                Console.Out.Flush();
                return 0;
            }
        }

        private sealed class VisitorWorkerResult
        {
            public int StatusCode { get; set; }
            public object Payload { get; set; }
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
            LogVisitorSyncStage(operationId, bookingId, "validation-complete;connect-start");

            // Stable key makes retries idempotent and does not depend on mutable visitor name or schedule.
            // This is a prototype-owned name namespace, not a replacement for the final approved data-ownership design.
            string entityKey = "STAMFORD VMS BOOKING " + bookingId;
            using (var engine = ConnectToGenetec())
            {
                try
                {
                    LogVisitorSyncStage(operationId, bookingId, "connected;directory-server-check-start");
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
                    LogVisitorSyncStage(operationId, bookingId, "directory-visitor-lookup-start");
                    var matches = FindVmsVisitorsByKey(engine, entityKey);
                    LogVisitorSyncStage(operationId, bookingId, "directory-visitor-lookup-complete;matchCount=" + matches.Count.ToString(CultureInfo.InvariantCulture));
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
                    LogVisitorSyncStage(operationId, bookingId, "sdk-transaction-start;operation=" + (created ? "create" : "update"));
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
                    LogVisitorSyncStage(operationId, bookingId, "sdk-transaction-complete;visitorGuid=" +
                        (visitor == null ? "unavailable" : visitor.Guid.ToString("D")));

                    // Force a server-side Visitor report query for this GUID before reading entity fields back.
                    // This distinguishes a successful Directory query from merely trusting the local UI state/cache.
                    LogVisitorSyncStage(operationId, bookingId, "server-report-readback-start;visitorGuid=" + visitor.Guid.ToString("D"));
                    if (!VisitorGuidExistsInServerQuery(engine, visitor.Guid))
                    {
                        throw new BridgeException(503, "VISITOR_REPORT_READBACK_FAILED",
                            "The native Visitor write was attempted, but a follow-up Visitor report did not return its GUID. Verify Genetec before retrying.");
                    }

                    LogVisitorSyncStage(operationId, bookingId, "server-report-readback-complete;entity-readback-start");
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

                    LogVisitorSyncStage(operationId, bookingId, "field-and-lifecycle-readback-verified;ledger-persist-start");
                    PersistBookingVisitorGuid(bookingId, verified.Guid);
                    LogVisitorSyncStage(operationId, bookingId, "complete;visitorGuid=" + verified.Guid.ToString("D"));
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
                    Console.Error.WriteLine("Native visitor sync failed: operation=" + operationId + " booking=" + bookingId + " exception=" + ex);
                    Console.Error.Flush();
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
            return _enableVisitorLifecycleSync;
        }

        private static bool IsAnyWriteScopeEnabled()
        {
            return IsTestWriteEnabled() || IsVisitorSyncEnabled() || IsVisitorLifecycleSyncEnabled();
        }

        private static string BuildEnabledWriteScopeDescription()
        {
            var scopes = new List<string>();
            if (IsTestWriteEnabled()) scopes.Add("fixed-test-cardholder-create-only");
            if (IsVisitorSyncEnabled()) scopes.Add("invited-vms-visitor-create-update-only");
            if (IsVisitorLifecycleSyncEnabled()) scopes.Add("synthetic-visitor-checkin-checkout-only");
            return scopes.Count == 0 ? "disabled" : String.Join(";", scopes);
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

        private static void LogVisitorSyncStage(string operationId, string bookingId, string stage)
        {
            // The booking ID is a prototype-generated synthetic key; never log guest name, credentials,
            // request body, image bytes, or the saved Genetec password.
            Console.WriteLine("NATIVE VISITOR SYNC STAGE: operation=" + operationId + " booking=" + bookingId +
                " stage=" + stage + " atUtc=" + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture));
            Console.Out.Flush();
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
            return _enableTestCardholderWrites;
        }

        private static bool IsVisitorSyncEnabled()
        {
            return _enableVisitorSync;
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
            byte[] buffer;
            try
            {
                buffer = Encoding.UTF8.GetBytes(Serializer.Serialize(payload));
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("HTTP JSON serialization failed: " + ex);
                Console.Error.Flush();
                statusCode = 500;
                buffer = Encoding.UTF8.GetBytes("{\"success\":false,\"errorCode\":\"BRIDGE_RESPONSE_SERIALIZATION_FAILED\",\"error\":\"The bridge could not serialize the response. Check the local bridge log before retrying.\"}");
            }

            try
            {
                response.StatusCode = statusCode;
                response.ContentType = "application/json; charset=utf-8";
                response.ContentEncoding = Encoding.UTF8;
                response.ContentLength64 = buffer.Length;
                using (var output = response.OutputStream)
                {
                    output.Write(buffer, 0, buffer.Length);
                }
            }
            catch (Exception ex)
            {
                // Client disconnects/connection resets are a normal possibility after a long SDK
                // operation. They must not unwind the HttpListener loop or kill the bridge process.
                Console.Error.WriteLine("HTTP response delivery failed: " + ex.GetType().Name + " — " + ex.Message);
                Console.Error.Flush();
                try { response.Abort(); } catch { }
            }
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

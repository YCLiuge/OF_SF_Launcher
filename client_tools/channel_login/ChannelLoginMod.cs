using System;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using MelonLoader;

[assembly: MelonInfo(typeof(OFChannelLogin.ChannelLoginMod), "OF Channel Login", "1.0.0", "OF_SF")]
[assembly: MelonPlatformDomain(MelonPlatformDomainAttribute.CompatibleDomains.IL2CPP)]

namespace OFChannelLogin
{
    /// <summary>
    /// Replaces the publisher channel login with the short-lived OF_SF private ticket.
    /// The original UILoginSystem still drives dispatch and the game gateway, but the
    /// QuickSDK / BiliBili / Steam SDK login panel is never opened.
    /// </summary>
    public sealed class ChannelLoginMod : MelonMod
    {
        private const BindingFlags Flags = BindingFlags.Public | BindingFlags.NonPublic |
                                            BindingFlags.Instance | BindingFlags.Static;

        private static string _slot = "";
        private static string _ticket = "";
        private static string _nickname = "";
        private static string _channel = "";

        private static PropertyInfo _handlerInstance = null!;
        private static PropertyInfo _mainSdk = null!;
        private static PropertyInfo _isInit = null!;
        private static PropertyInfo _uid = null!;
        private static PropertyInfo _token = null!;
        private static PropertyInfo _userName = null!;
        private static PropertyInfo _lastUid = null!;
        private static PropertyInfo _region = null!;
        private static MethodInfo _onLoginDone = null!;
        private static MethodInfo _onStartGame = null!;

        private static object? _ui;
        private static object? _sdk;
        private static bool _applied;
        private static bool _callbackSent;
        private static bool _entered;
        private static bool _entering;
        private static bool _installed;
        private static int _diagFrames;
        private static bool _objectsDumped;

        public override void OnInitializeMelon()
        {
            _slot = Environment.GetEnvironmentVariable("OF_SF_SLOT") ?? "";
            _ticket = Environment.GetEnvironmentVariable("OF_SF_TICKET") ?? "";
            _nickname = Environment.GetEnvironmentVariable("OF_SF_NICKNAME") ?? "";
            _channel = Environment.GetEnvironmentVariable("OF_SF_CHANNEL") ?? "";

            if (_slot.Length == 0 || _ticket.Length == 0)
            {
                MelonLogger.Msg("[OFChannelLogin] No private ticket in the environment; original channel login stays active.");
                return;
            }

            try
            {
                Install();
                _installed = true;
                if (string.Equals(_channel, "steam", StringComparison.OrdinalIgnoreCase))
                    ForceSteamYooAsset();
                MelonLogger.Msg("[OFChannelLogin] Private ticket installed for " + _channel + ".");
            }
            catch (Exception error)
            {
                MelonLogger.Error("[OFChannelLogin] Could not install: " + error.GetType().Name + ": " + error.Message);
            }
        }

        private static void DumpInitConfig()
        {
            try
            {
                var type = Resolve("Il2Cpp.InitConfigAsset");
                if (type == null)
                {
                    MelonLogger.Msg("[OFChannelLogin][diag] InitConfigAsset type missing");
                    return;
                }
                object? asset = null;
                try
                {
                    var get = type.GetMethod("GetAssetInstance", Flags);
                    if (get != null) asset = get.Invoke(null, null);
                }
                catch (Exception error) { MelonLogger.Msg("[OFChannelLogin][diag] GetAssetInstance " + error.GetType().Name); }
                if (asset == null)
                {
                    var instance = type.GetProperty("_Instance", Flags);
                    if (instance != null) asset = instance.GetValue(null);
                    else
                    {
                        var field = type.GetField("_Instance", Flags);
                        if (field != null) asset = field.GetValue(null);
                    }
                }
                if (asset == null)
                {
                    MelonLogger.Msg("[OFChannelLogin][diag] InitConfigAsset instance missing");
                    return;
                }
                var isEditor = type.GetMethod("IsEditorMode", Flags)?.Invoke(asset, null);
                MelonLogger.Msg("[OFChannelLogin][diag] IsEditorMode=" + isEditor);
                var configType = Resolve("Il2Cpp.InitConfigAsset+ConfigProperty") ??
                                 type.GetNestedType("ConfigProperty", Flags);
                if (configType == null)
                {
                    MelonLogger.Msg("[OFChannelLogin][diag] ConfigProperty missing");
                    return;
                }
                var getConfig = type.GetMethod("GetConfig", Flags);
                if (getConfig != null) LogConfig("GetConfig", getConfig.Invoke(asset, null), configType);
                foreach (var field in type.GetFields(Flags).Where(f => f.FieldType == configType))
                {
                    try { LogConfig("field " + field.Name, field.GetValue(asset), configType); }
                    catch (Exception error) { MelonLogger.Msg("[OFChannelLogin][diag] " + field.Name + " " + error.GetType().Name); }
                }
                try
                {
                    var app = Resolve("UnityEngine.Application");
                    var editor = app?.GetProperty("isEditor", Flags)?.GetValue(null);
                    var debug = Resolve("UnityEngine.Debug");
                    var debugBuild = debug?.GetProperty("isDebugBuild", Flags)?.GetValue(null);
                    MelonLogger.Msg("[OFChannelLogin][diag] Application.isEditor=" + editor + " Debug.isDebugBuild=" + debugBuild);
                }
                catch (Exception error) { MelonLogger.Msg("[OFChannelLogin][diag] runtime flags " + error.GetType().Name); }
            }
            catch (Exception error)
            {
                MelonLogger.Msg("[OFChannelLogin][diag] DumpInitConfig " + error.GetType().Name + ": " + error.Message);
            }
        }

        private static void LogConfig(string label, object? config, Type configType)
        {
            if (config == null)
            {
                MelonLogger.Msg("[OFChannelLogin][diag] " + label + "=<null>");
                return;
            }
            object? Field(string name)
            {
                try
                {
                    var property = configType.GetProperty(name, Flags);
                    if (property != null) return property.GetValue(config);
                    var field = configType.GetField(name, Flags);
                    return field?.GetValue(config);
                }
                catch (Exception) { return "<err>"; }
            }
            MelonLogger.Msg("[OFChannelLogin][diag] " + label + " mode=" + Field("YooAssetMode") +
                            " cdn=" + Field("CDNAddress") + " login=" + Field("LoginServer") +
                            " dev=" + Field("DevelopmentMode") + " version=" + Field("ProgramVersion"));
        }

        private static void ForceSteamYooAsset()
        {
            try
            {
                var type = Resolve("Il2Cpp.InitConfigAsset");
                if (type == null) return;
                var configType = Resolve("Il2Cpp.InitConfigAsset+ConfigProperty") ??
                                 type.GetNestedType("ConfigProperty", Flags);
                if (configType == null) return;
                object? asset = null;
                try { asset = type.GetMethod("GetAssetInstance", Flags)?.Invoke(null, null); }
                catch (Exception) { }
                if (asset == null)
                    asset = type.GetProperty("_Instance", Flags)?.GetValue(null) ??
                            type.GetField("_Instance", Flags)?.GetValue(null);
                if (asset == null) return;
                var login = Environment.GetEnvironmentVariable("OF_SF_LOGIN") ?? "";
                var cdn = Environment.GetEnvironmentVariable("OF_SF_CDN") ?? "";
                var mode = 1;
                var modeText = Environment.GetEnvironmentVariable("OF_SF_YOO_MODE");
                if (!string.IsNullOrEmpty(modeText)) int.TryParse(modeText, out mode);
                var version = "";
                try
                {
                    version = System.IO.File.ReadAllText(
                        System.IO.Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "version")).Trim();
                }
                catch (Exception) { }
                foreach (var name in new[] { "field_Public_ConfigProperty_0", "Release" })
                {
                    var property = type.GetProperty(name, Flags);
                    if (property == null)
                    {
                        MelonLogger.Msg("[OFChannelLogin][diag] config property missing: " + name);
                        continue;
                    }
                    MelonLogger.Msg("[OFChannelLogin][diag] config field " + name + " : " + property.PropertyType.FullName);
                    object? config;
                    try { config = property.GetValue(asset); }
                    catch (Exception error) { MelonLogger.Msg("[OFChannelLogin][diag] get " + name + " " + error.GetType().Name); continue; }
                    if (config == null)
                    {
                        MelonLogger.Msg("[OFChannelLogin][diag] " + name + "=<null>");
                        continue;
                    }
                    var actual = property.PropertyType;
                    SetConfig(config, actual, "YooAssetMode", mode);
                    if (cdn.Length != 0)
                    {
                        SetConfig(config, actual, "CDNAddress", cdn);
                        SetConfig(config, actual, "FallBackCDNAddress", cdn);
                    }
                    if (login.Length != 0) SetConfig(config, actual, "LoginServer", login);
                    if (version.Length != 0) SetConfig(config, actual, "ProgramVersion", version);
                    SetConfig(config, actual, "IsVerifyProgramVersion", false);
                    LogConfig("forced " + name, config, actual);
                }
                var getConfig = type.GetMethod("GetConfig", Flags);
                if (getConfig != null) LogConfig("GetConfig-after", getConfig.Invoke(asset, null), configType);
            }
            catch (Exception error)
            {
                MelonLogger.Msg("[OFChannelLogin][diag] ForceSteamYooAsset " + error.GetType().Name + ": " + error.Message);
            }
        }

        private static void SetConfig(object config, Type configType, string name, object value)
        {
            try
            {
                var property = configType.GetProperty(name, Flags);
                if (property != null && property.CanWrite)
                {
                    property.SetValue(config, property.PropertyType.IsEnum
                        ? Enum.ToObject(property.PropertyType, value) : value);
                    return;
                }
                var field = configType.GetField(name, Flags);
                if (field != null)
                    field.SetValue(config, field.FieldType.IsEnum
                        ? Enum.ToObject(field.FieldType, value) : value);
            }
            catch (Exception error)
            {
                MelonLogger.Msg("[OFChannelLogin][diag] set " + name + " " + error.GetType().Name);
            }
        }

        private static void Install()
        {
            var sdkHandler = Resolve("Il2Cpp.SDKHandler")
                ?? throw new TypeLoadException("Il2Cpp.SDKHandler");
            var loginUi = Resolve("Il2CppInutan.UILoginSystem")
                ?? throw new TypeLoadException("Il2CppInutan.UILoginSystem");
            var mainSdk = Resolve("Il2Cpp.MainSDK")
                ?? throw new TypeLoadException("Il2Cpp.MainSDK");

            _handlerInstance = Property(sdkHandler, "Instance");
            _mainSdk = Property(sdkHandler, "_mainSDK");
            _isInit = Property(mainSdk, "IsInit");
            _uid = Property(mainSdk, "SDKUid");
            _token = Property(mainSdk, "SDKToken");
            _userName = Property(mainSdk, "SDKUserName");
            _lastUid = Property(sdkHandler, "LastLoginSDKUID");
            _region = Property(loginUi, "RegionInfo");
            _onLoginDone = Method(loginUi, "OnSDKLoginDone");
            _onStartGame = Method(loginUi, "OnStartGame", typeof(string));

            foreach (var property in new[] { _isInit, _uid, _token, _userName })
            {
                if (!property.CanWrite)
                    throw new InvalidOperationException("MainSDK property is read-only: " + property.Name);
            }

            var harmony = new HarmonyLib.Harmony("OF_SF.ChannelLogin");
            Patch(harmony, Method(sdkHandler, "LoginSDK"), nameof(LoginPrefix));
            Patch(harmony, Method(loginUi, "OnReady"), nameof(ReadyPostfix), postfix: true);
            Patch(harmony, _onStartGame, nameof(StartPrefix));
            Patch(harmony, Method(loginUi, "Close"), nameof(ClosePrefix));

            // Let the SDK initialize, but suppress every channel's LoginSDK entry point.
            PatchSdkMethods(harmony, "Il2Cpp.BiliBiliSDK");
            PatchSdkMethods(harmony, "Il2Cpp.BiliBiliSDKImpl");
            PatchSdkMethods(harmony, "Il2Cpp.QuickSDK_Oversea_steam");
            PatchSdkMethods(harmony, "Il2Cpp.QuickSDK");

            // Native SDK callbacks must not reach the publisher after the private login.
            foreach (var typeName in new[]
                     {
                         "Il2Cpp.QuickSDK_cn_win", "Il2Cpp.QuickSDK_Oversea_steam",
                         "Il2Cpp.BiliBiliSDK", "Il2Cpp.BiliBiliSDKImpl"
                     })
            {
                var type = Resolve(typeName);
                if (type == null) continue;
                foreach (var name in new[] { "UploadRoleInfo", "OpenUserCenter" })
                {
                    foreach (var method in type.GetMethods(Flags).Where(m => m.Name == name))
                    {
                        try { Patch(harmony, method, nameof(SuppressPrefix)); }
                        catch (Exception) { /* optional method */ }
                    }
                }
            }
        }

        private static void PatchSdkMethods(HarmonyLib.Harmony harmony, string typeName)
        {
            var type = Resolve(typeName);
            if (type == null) return;
            foreach (var method in type.GetMethods(Flags).Where(m => m.Name == "LoginSDK"))
            {
                try { Patch(harmony, method, nameof(LoginPrefix)); }
                catch (Exception) { /* optional method */ }
            }
        }

        private static void Patch(HarmonyLib.Harmony harmony, MethodInfo method, string prefix, bool postfix = false)
        {
            if (method == null) return;
            var patch = new HarmonyMethod(typeof(ChannelLoginMod).GetMethod(prefix,
                BindingFlags.Static | BindingFlags.NonPublic)!);
            if (postfix) harmony.Patch(method, postfix: patch);
            else harmony.Patch(method, prefix: patch);
        }

        private static Type? Resolve(string name)
        {
            return AppDomain.CurrentDomain.GetAssemblies()
                .Select(assembly => assembly.GetType(name, false))
                .FirstOrDefault(type => type != null);
        }

        private static PropertyInfo Property(Type type, string name)
        {
            return type.GetProperty(name, Flags)
                   ?? throw new MissingMemberException(type.FullName, name);
        }

        private static MethodInfo Method(Type type, string name, params Type[] args)
        {
            return type.GetMethod(name, Flags, null, args, null)
                   ?? throw new MissingMethodException(type.FullName, name);
        }

        private static object? MainSdk()
        {
            if (_sdk != null) return _sdk;
            var handler = _handlerInstance.GetValue(null);
            if (handler == null) return null;
            _sdk = _mainSdk.GetValue(handler);
            return _sdk;
        }

        private static bool Apply()
        {
            if (_applied) return true;
            var sdk = MainSdk();
            if (sdk == null) return false;
            var handler = _handlerInstance.GetValue(null);
            try
            {
                _isInit.SetValue(sdk, true);
                _uid.SetValue(sdk, _slot);
                _token.SetValue(sdk, _ticket);
                if (_userName.CanWrite) _userName.SetValue(sdk, _nickname);
                if (_lastUid != null && _lastUid.CanWrite && handler != null)
                    _lastUid.SetValue(handler, _slot);
                _applied = true;
                MelonLogger.Msg("[OFChannelLogin] SDK fields set; notifying the game login screen.");
                return true;
            }
            catch (Exception error)
            {
                MelonLogger.Error("[OFChannelLogin] Could not set SDK fields: " + error.GetType().Name + ": " + error.Message);
                return false;
            }
        }

        private static bool Begin()
        {
            if (!_installed) return false;
            if (!Apply()) return false;
            if (_ui == null) return false;
            if (_callbackSent) return true;
            try
            {
                _callbackSent = true;
                _onLoginDone.Invoke(_ui, null);
                MelonLogger.Msg("[OFChannelLogin] Login callback delivered; waiting for region information.");
                return true;
            }
            catch (Exception error)
            {
                MelonLogger.Error("[OFChannelLogin] Login callback failed: " + error.GetType().Name);
                _callbackSent = false;
                _applied = false;
                return false;
            }
        }

        private static bool LoginPrefix()
        {
            Begin();
            return false;
        }

        private static void ReadyPostfix(object __instance)
        {
            _ui = __instance;
            _entered = false;
            if (!_callbackSent) Begin();
        }

        private static bool StartPrefix(object __instance)
        {
            if (_entering) return true;
            _ui = __instance;
            if (!_applied) Begin();
            else _entered = false; // explicit retry of the game gateway connection
            return false;
        }

        private static bool ClosePrefix()
        {
            _ui = null;
            return true;
        }

        private static bool SuppressPrefix() => false;

        private static bool FindLoginUi()
        {
            try
            {
                var uiType = Resolve("Il2CppInutan.UILoginSystem");
                var objectType = Resolve("UnityEngine.Object");
                if (uiType == null || objectType == null) return false;
                var method = objectType.GetMethods(Flags).FirstOrDefault(m =>
                    m.Name == "FindObjectsOfTypeAll" && m.IsGenericMethodDefinition);
                if (method == null) return false;
                var found = method.MakeGenericMethod(uiType).Invoke(null, null) as Array;
                if (found == null || found.Length == 0) return false;
                _ui = found.GetValue(0);
                MelonLogger.Msg("[OFChannelLogin][diag] found UILoginSystem instance");
                if (!_callbackSent) Begin();
                return _ui != null;
            }
            catch (Exception error)
            {
                MelonLogger.Msg("[OFChannelLogin][diag] FindLoginUi " + error.GetType().Name);
                return false;
            }
        }

        private static void DumpObjects()
        {
            try
            {
                MelonLogger.Msg("[OFChannelLogin][diag] DumpObjects begin");
                var sm = Resolve("UnityEngine.SceneManagement.SceneManager");
                var getActive = sm?.GetMethod("GetActiveScene", Flags, null, Type.EmptyTypes, null);
                if (getActive != null)
                {
                    var scene = getActive.Invoke(null, null);
                    var name = scene?.GetType().GetProperty("name", Flags)?.GetValue(scene);
                    MelonLogger.Msg("[OFChannelLogin][diag] active scene=" + name);
                }
                var objectType = Resolve("UnityEngine.Object");
                var gameObjectType = Resolve("UnityEngine.GameObject");
                if (objectType == null || gameObjectType == null) return;
                var method = objectType.GetMethods(Flags).FirstOrDefault(m =>
                    m.Name == "FindObjectsOfTypeAll" && m.IsGenericMethodDefinition);
                if (method == null) return;
                var found = method.MakeGenericMethod(gameObjectType).Invoke(null, null) as Array;
                if (found == null) return;
                MelonLogger.Msg("[OFChannelLogin][diag] objects=" + found.Length);
                var shown = 0;
                foreach (var item in found)
                {
                    if (shown >= 25) break;
                    var name = item?.GetType().GetProperty("name", Flags)?.GetValue(item) as string;
                    if (string.IsNullOrEmpty(name)) continue;
                    if (name.IndexOf("Login", StringComparison.OrdinalIgnoreCase) >= 0 ||
                        name.IndexOf("UI_Root", StringComparison.OrdinalIgnoreCase) >= 0 ||
                        name.IndexOf("Logo", StringComparison.OrdinalIgnoreCase) >= 0)
                    {
                        MelonLogger.Msg("[OFChannelLogin][diag] object " + name);
                        shown++;
                    }
                }
            }
            catch (Exception error)
            {
                MelonLogger.Msg("[OFChannelLogin][diag] DumpObjects " + error.GetType().Name);
            }
        }

        public override void OnUpdate()
        {
            if (!_installed || _ui == null || !_applied || _entered) return;
            try
            {
                if (_region.GetValue(_ui) != null)
                {
                    _entered = true;
                    _entering = true;
                    try
                    {
                        _onStartGame.Invoke(_ui, new object[] { _slot });
                        MelonLogger.Msg("[OFChannelLogin] Game gateway start requested.");
                    }
                    finally { _entering = false; }
                }
            }
            catch (Exception error)
            {
                MelonLogger.Error("[OFChannelLogin] Update failed: " + (error.InnerException ?? error).GetType().Name);
            }
        }
    }
}

using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using Microsoft.Win32;

public class VoiceSettings {
    public string home_assistant_url { get; set; }
    public string pipeline_name { get; set; }
}

public class VoiceWindow : Form {
    [DllImport("user32.dll", SetLastError=true)]
    static extern bool RegisterHotKey(IntPtr window, int id, uint modifiers, uint key);
    [DllImport("user32.dll")]
    static extern bool UnregisterHotKey(IntPtr window, int id);
    readonly WebView2 browser = new WebView2();
    readonly Label status = new Label();
    readonly NotifyIcon tray = new NotifyIcon();
    readonly VoiceSettings settings;
    readonly Uri home;
    readonly string root;
    readonly bool remoteMode;
    readonly Button keyboard = new Button();
    readonly WakeBridge wake;
    ToolStripMenuItem wakeMenu;
    readonly Dictionary<string,string> remoteBinding;
    bool remoteBusy;
    string remoteRequest;
    TaskCompletionSource<string> remoteReply;
    bool ready, busy, exiting, hotkey;
    DateTime requested = DateTime.MinValue;
    long talkGeneration;
    bool wakeOriginTurn, captureAdmitted;

    public VoiceWindow(string directory, bool remote) {
        root = directory;
        remoteMode = remote;
        settings = new JavaScriptSerializer().Deserialize<VoiceSettings>(File.ReadAllText(Path.Combine(root,"settings.json")));
        home = new Uri(settings.home_assistant_url);
        if (home.Scheme != "https" || !String.IsNullOrEmpty(home.UserInfo))
            throw new InvalidOperationException("Use the existing secure Home Assistant address.");
        if (remoteMode) {
            remoteBinding = new JavaScriptSerializer().Deserialize<Dictionary<string,string>>(File.ReadAllText(Path.Combine(root,"lg-remote.json")));
            foreach(string key in new[]{"entity_id","device_id","config_entry_id","area_id"})
                if(!remoteBinding.ContainsKey(key) || String.IsNullOrEmpty(remoteBinding[key])) throw new InvalidOperationException("Remote mapping is incomplete.");
            if (!System.Text.RegularExpressions.Regex.IsMatch(remoteBinding["entity_id"],@"^media_player\.[a-z0-9_]+$")) throw new InvalidOperationException("Remote mapping is invalid.");
        }
        Text = remoteMode ? "Master Bedroom TV Remote" : "Nimo Voice";
        Size = remoteMode ? new Size(1100,900) : new Size(560,780);
        StartPosition = FormStartPosition.CenterScreen;
        Icon = SystemIcons.Information;
        browser.Dock = DockStyle.Fill;
        status.Dock = DockStyle.Bottom;
        status.Height = 44;
        status.TextAlign = ContentAlignment.MiddleCenter;
        status.Text = "Sign in to Home Assistant, then press Alt+V to talk.";
        Controls.Add(browser); Controls.Add(status);
        if(remoteMode) {
            keyboard.Text="Keyboard remote — click to focus";
            keyboard.AccessibleName="Keyboard remote";
            keyboard.AccessibleDescription="Arrows move the TV selection; Enter is OK, Escape is Back, Home opens the TV home menu, and digits enter numbers. Click the page to stop keyboard control.";
            keyboard.Dock=DockStyle.Top; keyboard.Height=42;
            keyboard.GotFocus += (s,e) => { status.Text="Master Bedroom TV: arrows move, Enter = OK, Esc = Back, Home = Home, 0–9 = numbers. Click the page to release keys."; };
            keyboard.LostFocus += async (s,e) => await CancelRemoteKeys();
            Controls.Add(keyboard);
            status.Text="Click Keyboard remote for arrows, Enter and Esc; click the page to release keys.";
        }
        if(!remoteMode) {
            wake=new WakeBridge(this,root,WakeIdle,async () => await Talk(true),WakeStatus);
            SystemEvents.SessionSwitch += SessionChanged;
            SystemEvents.PowerModeChanged += PowerChanged;
        }
        var menu = new ContextMenuStrip();
        menu.Items.Add("Talk to Home Assistant (Alt+V)", null, async (s,e) => await Talk());
        if(!remoteMode) {
            wakeMenu=new ToolStripMenuItem("Enable OK Nabu (microphone)");
            wakeMenu.Enabled=wake.Prepared;
            wakeMenu.Click += async (s,e) => { wakeMenu.Enabled=false;try { await wake.ToggleAsync(); } finally { if(!IsDisposed) wakeMenu.Enabled=wake.Prepared; } };
            menu.Items.Add(wakeMenu);
        }
        menu.Items.Add("Open", null, (s,e) => { Show(); WindowState=FormWindowState.Normal; Activate(); });
        menu.Items.Add("Exit", null, (s,e) => { exiting=true; Close(); });
        tray.Icon=Icon; tray.Text="Nimo Voice — Alt+V"; tray.ContextMenuStrip=menu; tray.Visible=!remoteMode;
        tray.DoubleClick += async (s,e) => await Talk();
        FormClosing += async (s,e) => { if (!remoteMode && !exiting && e.CloseReason==CloseReason.UserClosing) { e.Cancel=true; talkGeneration++; wakeOriginTurn=false; captureAdmitted=false; await StopListening(); if(wake!=null) wake.TurnClosed(); Hide(); } };
        Shown += async (s,e) => await InitializeBrowser();
    }

    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        if (!remoteMode) {
            hotkey=RegisterHotKey(Handle, 1, 0x4001, (uint)Keys.V); // Alt + no repeat
            if (!hotkey) status.Text="Alt+V is already in use. Exit the conflicting shortcut before starting Nimo Voice.";
        }
        WriteState();
    }
    protected override void WndProc(ref Message m) {
        if (m.Msg==0x0312 && m.WParam.ToInt32()==1) {
            BeginInvoke(new Action(async () => await Talk()));
            return;
        }
        base.WndProc(ref m);
    }
    protected override void OnFormClosed(FormClosedEventArgs e) {
        talkGeneration++;
        if (hotkey) UnregisterHotKey(Handle,1);
        if(wake!=null) {
            SystemEvents.SessionSwitch -= SessionChanged;
            SystemEvents.PowerModeChanged -= PowerChanged;
            wake.Dispose();
        }
        tray.Dispose(); browser.Dispose();
        base.OnFormClosed(e);
    }
    protected override bool ProcessCmdKey(ref Message msg, Keys keyData) {
        if(remoteMode && keyboard.Focused && (keyData & Keys.Modifiers)==Keys.None) {
            string key=null;
            switch(keyData & Keys.KeyCode) {
                case Keys.Up:key="UP";break;case Keys.Down:key="DOWN";break;
                case Keys.Left:key="LEFT";break;case Keys.Right:key="RIGHT";break;
                case Keys.Enter:key="ENTER";break;case Keys.Escape:key="BACK";break;case Keys.Home:key="HOME";break;
                default:
                    var code=keyData & Keys.KeyCode;
                    if(code>=Keys.D0 && code<=Keys.D9) key=((int)(code-Keys.D0)).ToString();
                    if(code>=Keys.NumPad0 && code<=Keys.NumPad9) key=((int)(code-Keys.NumPad0)).ToString();
                    break;
            }
            if(key!=null) {
                // A held key is not a new command. Never queue or retry it.
                if((msg.LParam.ToInt64() & (1L<<30))==0 && !remoteBusy)
                    BeginInvoke(new Action(async () => await SendRemoteKey(key)));
                return true;
            }
        }
        return base.ProcessCmdKey(ref msg,keyData);
    }
    async Task CancelRemoteKeys() {
        if(browser.CoreWebView2==null) return;
        try { await browser.CoreWebView2.ExecuteScriptAsync("window.edsysLGRemoteEpoch=(window.edsysLGRemoteEpoch||0)+1"); } catch { }
    }
    async Task SendRemoteKey(string key) {
        if(remoteBusy || !keyboard.Focused || !ready || browser.CoreWebView2==null) return;
        remoteBusy=true;
        try {
            var serializer=new JavaScriptSerializer();
            var script=File.ReadAllText(Path.Combine(root,"lg-key.js")).Replace("__LG_BINDING__",serializer.Serialize(remoteBinding)).Replace("__LG_BUTTON__",serializer.Serialize(key));
            remoteRequest=Guid.NewGuid().ToString("N");
            remoteReply=new TaskCompletionSource<string>(TaskCreationOptions.RunContinuationsAsynchronously);
            // ExecuteScriptAsync serializes a Promise rather than awaiting it.
            // The existing same-origin message channel returns its async result.
            var prefix=serializer.Serialize("lg-key:"+remoteRequest+":");
            script += ".then(result=>window.chrome.webview.postMessage("+prefix+"+result)).catch(()=>window.chrome.webview.postMessage("+prefix+"+'remote-uncertain'));";
            await browser.CoreWebView2.ExecuteScriptAsync(script);
            var completed=await Task.WhenAny(remoteReply.Task,Task.Delay(11000));
            var result=completed==remoteReply.Task ? await remoteReply.Task : "remote-uncertain";
            status.Text=result=="remote-sent" ? "Key sent to Master Bedroom TV. Click the page to release keyboard control."
                : result=="remote-tv-off" ? "Wake the Master Bedroom TV before using navigation keys."
                : result=="remote-dialog-open" ? "Close the Home Assistant dialog before using the TV keyboard."
                : result=="remote-mapping-changed" ? "The TV mapping changed and needs review."
                : result=="remote-offline" ? "Home Assistant is disconnected. No key was sent."
                : result=="remote-uncertain" ? "Key outcome is uncertain. Check the TV before pressing again."
                : "Open the Master Bedroom remote and try a fresh key when it is ready.";
        } catch { status.Text="Key outcome is uncertain. Check the TV before pressing again."; }
        finally { remoteBusy=false; remoteRequest=null; remoteReply=null; WriteState(); }
    }
    bool SameOrigin(string value) {
        Uri target;
        return Uri.TryCreate(value,UriKind.Absolute,out target) && target.Scheme=="https" && target.Authority.Equals(home.Authority,StringComparison.OrdinalIgnoreCase);
    }
    async Task InitializeBrowser() {
        try {
            var options=new CoreWebView2EnvironmentOptions("--autoplay-policy=no-user-gesture-required");
            var env=await CoreWebView2Environment.CreateAsync(null,Path.Combine(root,"browser-profile"),options);
            await browser.EnsureCoreWebView2Async(env);
            if(!remoteMode) await browser.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(File.ReadAllText(Path.Combine(root,"wake-capture.js")));
            browser.CoreWebView2.Settings.AreDevToolsEnabled=false;
            browser.CoreWebView2.Settings.IsStatusBarEnabled=false;
            browser.CoreWebView2.NavigationStarting += (s,e) => { if(!SameOrigin(e.Uri)) e.Cancel=true; };
            browser.CoreWebView2.NewWindowRequested += (s,e) => { e.Handled=true; };
            browser.CoreWebView2.WebMessageReceived += (s,e) => {
                if (!SameOrigin(e.Source)) return;
                string value=e.TryGetWebMessageAsString();
                if(remoteMode) {
                    string prefix="lg-key:"+remoteRequest+":";
                    if(remoteReply!=null && value.StartsWith(prefix,StringComparison.Ordinal)) remoteReply.TrySetResult(value.Substring(prefix.Length));
                    return;
                }
                if(value.StartsWith("wake-capture:",StringComparison.Ordinal)) { ReleaseWakeCapture(value.Substring(13));return; }
                if(value=="wake-dialog-closed") { talkGeneration++;wakeOriginTurn=false;captureAdmitted=false;if(wake!=null) wake.TurnClosed();return; }
                if(wake!=null && (value=="pipeline-missing" || value=="assist-unavailable" || value=="assist-button-not-ready" || value=="sign-in-or-home-required")) { wakeOriginTurn=false;captureAdmitted=false;wake.TurnStartedOrFailed(); }
                if (value=="camera-opening") Size=new Size(1100,850);
                status.Text=value=="requested" ? "Speak after the microphone opens. Alt+V starts another turn."
                    : value=="pipeline-missing" ? "The configured voice assistant is unavailable. Check Home Assistant."
                    : value=="assist-unavailable" ? "Home Assistant is unavailable. Check your connection, then press Alt+V."
                    : value=="camera-opening" ? "Opening the selected Home Assistant camera…"
                    : value=="camera-playing" ? "Live camera video is playing. Close the window to stop viewing."
                    : value=="camera-image-visible" ? "Camera image is visible; live video has not been confirmed."
                    : value=="camera-failed" ? "Camera video could not be verified. Check the Home Assistant camera view."
                    : "Sign in to Home Assistant and open Home, then press Alt+V.";
                WriteState();
            };
            browser.CoreWebView2.PermissionRequested += (s,e) => {
                bool allow=!remoteMode && e.PermissionKind==CoreWebView2PermissionKind.Microphone && SameOrigin(e.Uri)
                    && (e.IsUserInitiated || (DateTime.UtcNow-requested).TotalSeconds<30);
                e.State=allow ? CoreWebView2PermissionState.Allow : CoreWebView2PermissionState.Deny;
                e.SavesInProfile=false;
            };
            browser.CoreWebView2.NavigationCompleted += (s,e) => {
                ready=e.IsSuccess;
                if (!ready) status.Text="Home Assistant is unavailable. Check your connection, then press Alt+V.";
                WriteState();
            };
            browser.CoreWebView2.Navigate(new Uri(home,remoteMode ? "/edsys-media/master-bedroom" : "/edsys-home/home").ToString());
        } catch { status.Text="Could not open Home Assistant. Check the secure address and WebView2 installation."; WriteState(); }
    }
    async Task StopListening() {
        if(browser.CoreWebView2==null || !ready) return;
        try { await browser.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(Path.Combine(root,"stop-assist.js"))); }
        catch { }
    }

    async Task<bool> WakeIdle() {
        if(remoteMode || busy || !ready || browser.CoreWebView2==null || IsDisposed) return false;
        try {
            await browser.CoreWebView2.ExecuteScriptAsync("window.edsysWakeCapture && window.edsysWakeCapture.enable(true)");
            var result=await browser.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(Path.Combine(root,"wake-idle.js")));
            return result=="true";
        } catch { return false; }
    }
    async void ReleaseWakeCapture(string request) {
        if(remoteMode || wake==null || !System.Text.RegularExpressions.Regex.IsMatch(request,@"^[a-f0-9]{32}_[1-9][0-9]{0,9}$")) return;
        try {
            long generation=talkGeneration;
            bool released=await wake.BeforeTalkAsync();
            if(browser.CoreWebView2==null || IsDisposed) return;
            browser.CoreWebView2.PostWebMessageAsJson(new JavaScriptSerializer().Serialize(new {type="wake-capture-release",id=request,allow=released && captureAdmitted && Visible && !exiting && generation==talkGeneration}));
        } catch { /* No grant means the browser denies this capture after timeout. */ }
    }
    async void WakeStatus(string value) {
        if(wakeMenu!=null) wakeMenu.Text=wake.Enabled ? "Disable OK Nabu" : "Enable OK Nabu (microphone)";
        tray.Text=wake.Enabled ? "Nimo Voice — OK Nabu / Alt+V" : "Nimo Voice — Alt+V";
        if(!wake.Enabled && browser.CoreWebView2!=null) {
            if(wakeOriginTurn) { talkGeneration++;wakeOriginTurn=false;captureAdmitted=false;await StopListening(); }
            try { await browser.CoreWebView2.ExecuteScriptAsync("window.edsysWakeCapture && window.edsysWakeCapture.enable(false)"); } catch { }
        }
        if(!IsDisposed) WriteState();
    }
    void SessionChanged(object sender,SessionSwitchEventArgs e) {
        if(e.Reason!=SessionSwitchReason.SessionUnlock && e.Reason!=SessionSwitchReason.SessionLogon) {
            try { BeginInvoke(new Action(async () => await wake.DisableAsync())); } catch(InvalidOperationException) { }
        }
    }
    void PowerChanged(object sender,PowerModeChangedEventArgs e) {
        if(e.Mode==PowerModes.Suspend) {
            try { BeginInvoke(new Action(async () => await wake.DisableAsync())); } catch(InvalidOperationException) { }
        }
    }
    async Task Talk(bool fromWake=false) {
        if(busy) return;
        busy=true;
        long generation=++talkGeneration;
        wakeOriginTurn=fromWake;
        try {
            // The optional detector must release capture before native Assist.
            if(wake!=null && !await wake.BeforeTalkAsync()) {
                status.Text="OK Nabu could not release the microphone. Turn it off, then try Alt+V.";return;
            }
            if(exiting || IsDisposed || generation!=talkGeneration || (fromWake && (wake==null || !wake.Enabled))) return;
            Show(); WindowState=FormWindowState.Normal; Activate();
            if(browser.CoreWebView2==null) { status.Text="Opening Home Assistant…";if(wake!=null) wake.TurnStartedOrFailed();return; }
            if(!ready) { browser.CoreWebView2.Reload();if(wake!=null) wake.TurnStartedOrFailed();return; }
            requested=DateTime.UtcNow;captureAdmitted=true;
            await browser.CoreWebView2.ExecuteScriptAsync("window.edsysWakeCapture && window.edsysWakeCapture.begin()");
            if(exiting || IsDisposed || generation!=talkGeneration || (fromWake && (wake==null || !wake.Enabled))) return;
            await browser.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(Path.Combine(root,"camera-view.js")));
            if(exiting || IsDisposed || generation!=talkGeneration || (fromWake && (wake==null || !wake.Enabled))) return;
            var name=new JavaScriptSerializer().Serialize(settings.pipeline_name);
            string js=File.ReadAllText(Path.Combine(root,"start-assist.js")).Replace("__PIPELINE_NAME__",name);
            status.Text="Opening the microphone…";
            await browser.CoreWebView2.ExecuteScriptAsync(js);
        } catch { captureAdmitted=false;wakeOriginTurn=false;status.Text="Voice could not start. Check Home Assistant, then press Alt+V.";if(wake!=null) wake.TurnStartedOrFailed(); }
        finally { busy=false;WriteState(); }
    }
    void WriteState() {
        var data=new { hotkey_registered=hotkey, browser_ready=ready, status=status.Text, version="0.4.0", wake_enabled=wake!=null && wake.Enabled, wake_state=wake==null ? "off" : wake.Status };
        File.WriteAllText(Path.Combine(root,remoteMode ? "remote-status.json" : "status.json"),new JavaScriptSerializer().Serialize(data));
    }
}

public static class Program {
    [DllImport("user32.dll", CharSet=CharSet.Unicode)]
    static extern IntPtr FindWindow(string className,string title);
    [DllImport("user32.dll")]
    static extern bool ShowWindow(IntPtr window,int command);
    [DllImport("user32.dll")]
    static extern bool SetForegroundWindow(IntPtr window);
    [STAThread]
    public static void Main(string[] args) {
        bool remote=args.Length==1 && args[0]=="--remote";
        if(args.Length>0 && !remote) return;
        bool created;
        using(var instance=new Mutex(true,remote ? "Local\\EdSysNimoLGRemote" : "Local\\EdSysNimoVoice",out created)) {
            if (!created) {
                if(remote) {
                    var existing=FindWindow(null,"Master Bedroom TV Remote");
                    if(existing!=IntPtr.Zero) { ShowWindow(existing,9); SetForegroundWindow(existing); }
                }
                return;
            }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try { Application.Run(new VoiceWindow(AppDomain.CurrentDomain.BaseDirectory,remote)); }
            catch { MessageBox.Show("Nimo Voice could not start. Check its private settings and installed files.","Nimo Voice"); }
        }
    }
}

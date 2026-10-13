using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

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
    bool ready, busy, exiting, hotkey;
    DateTime requested = DateTime.MinValue;

    public VoiceWindow(string directory) {
        root = directory;
        settings = new JavaScriptSerializer().Deserialize<VoiceSettings>(File.ReadAllText(Path.Combine(root,"settings.json")));
        home = new Uri(settings.home_assistant_url);
        if (home.Scheme != "https" || !String.IsNullOrEmpty(home.UserInfo))
            throw new InvalidOperationException("Use the existing secure Home Assistant address.");
        Text = "Nimo Voice";
        Size = new Size(560,780);
        StartPosition = FormStartPosition.CenterScreen;
        Icon = SystemIcons.Information;
        browser.Dock = DockStyle.Fill;
        status.Dock = DockStyle.Bottom;
        status.Height = 44;
        status.TextAlign = ContentAlignment.MiddleCenter;
        status.Text = "Sign in to Home Assistant, then press Alt+V to talk.";
        Controls.Add(browser); Controls.Add(status);
        var menu = new ContextMenuStrip();
        menu.Items.Add("Talk to Home Assistant (Alt+V)", null, async (s,e) => await Talk());
        menu.Items.Add("Open", null, (s,e) => { Show(); WindowState=FormWindowState.Normal; Activate(); });
        menu.Items.Add("Exit", null, (s,e) => { exiting=true; Close(); });
        tray.Icon=Icon; tray.Text="Nimo Voice — Alt+V"; tray.ContextMenuStrip=menu; tray.Visible=true;
        tray.DoubleClick += async (s,e) => await Talk();
        FormClosing += async (s,e) => { if (!exiting && e.CloseReason==CloseReason.UserClosing) { e.Cancel=true; await StopListening(); Hide(); } };
        Shown += async (s,e) => await InitializeBrowser();
    }

    protected override void OnHandleCreated(EventArgs e) {
        base.OnHandleCreated(e);
        hotkey=RegisterHotKey(Handle, 1, 0x4001, (uint)Keys.V); // Alt + no repeat
        if (!hotkey) status.Text="Alt+V is already in use. Exit the conflicting shortcut before starting Nimo Voice.";
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
        if (hotkey) UnregisterHotKey(Handle,1);
        tray.Dispose(); browser.Dispose();
        base.OnFormClosed(e);
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
            browser.CoreWebView2.Settings.AreDevToolsEnabled=false;
            browser.CoreWebView2.Settings.IsStatusBarEnabled=false;
            browser.CoreWebView2.NavigationStarting += (s,e) => { if(!SameOrigin(e.Uri)) e.Cancel=true; };
            browser.CoreWebView2.NewWindowRequested += (s,e) => { e.Handled=true; };
            browser.CoreWebView2.WebMessageReceived += (s,e) => {
                if (!SameOrigin(e.Source)) return;
                string value=e.TryGetWebMessageAsString();
                status.Text=value=="requested" ? "Speak after the microphone opens. Alt+V starts another turn."
                    : value=="pipeline-missing" ? "The configured voice assistant is unavailable. Check Home Assistant."
                    : value=="assist-unavailable" ? "Home Assistant is unavailable. Check your connection, then press Alt+V."
                    : "Sign in to Home Assistant and open Home, then press Alt+V.";
                WriteState();
            };
            browser.CoreWebView2.PermissionRequested += (s,e) => {
                bool allow=e.PermissionKind==CoreWebView2PermissionKind.Microphone && SameOrigin(e.Uri)
                    && (e.IsUserInitiated || (DateTime.UtcNow-requested).TotalSeconds<30);
                e.State=allow ? CoreWebView2PermissionState.Allow : CoreWebView2PermissionState.Deny;
                e.SavesInProfile=false;
            };
            browser.CoreWebView2.NavigationCompleted += (s,e) => {
                ready=e.IsSuccess;
                if (!ready) status.Text="Home Assistant is unavailable. Check your connection, then press Alt+V.";
                WriteState();
            };
            browser.CoreWebView2.Navigate(new Uri(home,"/edsys-home/home").ToString());
        } catch { status.Text="Could not open Home Assistant. Check the secure address and WebView2 installation."; WriteState(); }
    }
    async Task StopListening() {
        if(browser.CoreWebView2==null || !ready) return;
        try { await browser.CoreWebView2.ExecuteScriptAsync(File.ReadAllText(Path.Combine(root,"stop-assist.js"))); }
        catch { }
    }

    async Task Talk() {
        Show(); WindowState=FormWindowState.Normal; Activate();
        if (busy) return;
        if (browser.CoreWebView2==null) { status.Text="Opening Home Assistant…"; return; }
        if (!ready) { browser.CoreWebView2.Reload(); return; }
        busy=true; requested=DateTime.UtcNow;
        try {
            var name=new JavaScriptSerializer().Serialize(settings.pipeline_name);
            string js=File.ReadAllText(Path.Combine(root,"start-assist.js")).Replace("__PIPELINE_NAME__",name);
            status.Text="Opening the microphone…";
            await browser.CoreWebView2.ExecuteScriptAsync(js);
            WriteState();
        } catch { status.Text="Voice could not start. Check Home Assistant, then press Alt+V."; WriteState(); }
        finally { busy=false; }
    }
    void WriteState() {
        var data=new { hotkey_registered=hotkey, browser_ready=ready, status=status.Text, version="0.1.0" };
        File.WriteAllText(Path.Combine(root,"status.json"),new JavaScriptSerializer().Serialize(data));
    }
}

public static class Program {
    [STAThread]
    public static void Main() {
        bool created;
        using(var instance=new Mutex(true,"Local\\EdSysNimoVoice",out created)) {
            if (!created) return;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try { Application.Run(new VoiceWindow(AppDomain.CurrentDomain.BaseDirectory)); }
            catch { MessageBox.Show("Nimo Voice could not start. Check its private settings and installed files.","Nimo Voice"); }
        }
    }
}

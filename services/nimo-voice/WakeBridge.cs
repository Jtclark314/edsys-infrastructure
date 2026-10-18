using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

// All transitions run on the owner form's UI thread. Epochs retire late child
// messages; a wake event is usable only after the child releases its microphone.
public sealed class WakeEpoch {
    public bool Enabled { get; private set; }
    public int Epoch { get; private set; }
    public string Phase { get; private set; }
    public WakeEpoch() { Phase="off"; }
    public void Enable() { Enabled=true; Phase="starting"; }
    public void Ready() { if(Enabled && Phase=="starting") Phase="disarmed"; }
    public int Command(bool arm) { Epoch++; Phase=arm ? "arming" : "disarming"; return Epoch; }
    public bool Acknowledge(string value,int epoch) {
        if(epoch!=Epoch) return false;
        if(value=="armed" && Enabled && Phase=="arming") { Phase="armed";return true; }
        if(value=="disarmed" && Phase=="disarming") { Phase="disarmed";return true; }
        return false;
    }
    public bool Wake(int epoch) {
        if(!Enabled || Phase!="armed" || epoch!=Epoch) return false;
        Phase="disarmed";return true;
    }
    public void Disable() { Enabled=false;Epoch++;Phase="off"; }
}

public sealed class WakeBridge : IDisposable {
    readonly Form owner;
    readonly string root;
    readonly Func<Task<bool>> idle;
    readonly Func<Task> talk;
    readonly Action<string> changed;
    readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
    readonly SemaphoreSlim commandLock = new SemaphoreSlim(1,1);
    readonly WakeEpoch gate = new WakeEpoch();
    Process child;
    TaskCompletionSource<bool> readyReply, commandReply;
    int commandEpoch;
    bool ticking, turnHold, disposed;
    DateTime notBefore=DateTime.MinValue;
    string lastStatus="off";
    public bool Enabled { get { return gate.Enabled; } }
    public string Status { get { return lastStatus; } }
    public bool Prepared { get {
        return File.Exists(Path.Combine(root,"wake-runtime", "Scripts", "python.exe"))
            && File.Exists(Path.Combine(root,"wake", "wake_worker.py"))
            && File.Exists(Path.Combine(root,"wake", "models.lock.json"))
            && File.Exists(Path.Combine(root,"wake-idle.js"))
            && File.Exists(Path.Combine(root,"wake-capture.js"));
    } }
    public WakeBridge(Form form,string directory,Func<Task<bool>> isIdle,Func<Task> start,Action<string> status) {
        owner=form;root=directory;idle=isIdle;talk=start;changed=status;
        timer.Interval=1000;
        timer.Tick += async (s,e) => await Tick();
    }
    void Publish(string value) {
        lastStatus=value;
        try { File.WriteAllText(Path.Combine(root,"wake-status.json"),
            new JavaScriptSerializer().Serialize(new {enabled=Enabled,state=value,wake_word="OK Nabu",version=1})); } catch { }
        changed(value);
    }
    static string Quote(string value) {
        if(value.IndexOf('"')>=0 || value.IndexOf('\n')>=0 || value.IndexOf('\r')>=0)
            throw new InvalidOperationException("Invalid worker path");
        return "\""+value+"\"";
    }
    public async Task ToggleAsync() {
        if(Enabled) { await DisableAsync();return; }
        if(disposed || !Prepared) { Publish("not-prepared");return; }
        if(child!=null && !StopChild()) { Publish("release-uncertain");return; }
        gate.Enable();turnHold=false;notBefore=DateTime.UtcNow.AddSeconds(2);
        readyReply=new TaskCompletionSource<bool>();
        var process=new Process();
        process.StartInfo=new ProcessStartInfo {
            FileName=Path.Combine(root,"wake-runtime", "Scripts", "python.exe"),
            Arguments="-I "+Quote(Path.Combine(root,"wake", "wake_worker.py"))+" --models-dir "+Quote(Path.Combine(root,"wake-models")),
            WorkingDirectory=Path.Combine(root,"wake"),UseShellExecute=false,CreateNoWindow=true,
            RedirectStandardInput=true,RedirectStandardOutput=true,RedirectStandardError=true
        };
        process.EnableRaisingEvents=true;
        process.OutputDataReceived += (s,e) => {
            if(e.Data==null) return;
            Dispatch(() => { if(child==process) Handle(e.Data); });
        };
        // Drain bounded OS pipe; never retain or expose backend exception text.
        process.ErrorDataReceived += (s,e) => { };
        process.Exited += (s,e) => Dispatch(() => {
            if(child!=process) return;
            readyReply.TrySetResult(false);
            if(commandReply!=null) commandReply.TrySetResult(false);
            gate.Disable();timer.Stop();Publish("worker-stopped");
        });
        try {
            child=process;process.Start();process.BeginOutputReadLine();process.BeginErrorReadLine();Publish("loading");
            if(await Task.WhenAny(readyReply.Task,Task.Delay(20000))!=readyReply.Task || !await readyReply.Task)
                throw new InvalidOperationException("Wake worker did not become ready");
            if(!Enabled) { StopChild();return; }
            timer.Start();Publish("waiting-for-idle");
        } catch { gate.Disable();StopChild();Publish("unavailable"); }
    }
    void Dispatch(Action action) {
        if(disposed || owner.IsDisposed || !owner.IsHandleCreated) return;
        try { owner.BeginInvoke(action); } catch(InvalidOperationException) { }
    }
    void Handle(string line) {
        try {
            if(line.Length>2048) throw new InvalidOperationException();
            var data=new JavaScriptSerializer().Deserialize<Dictionary<string,object>>(line);
            if(data==null || !data.ContainsKey("event") || !data.ContainsKey("epoch")) throw new InvalidOperationException();
            var value=data["event"] as string;
            if(!(data["epoch"] is int)) throw new InvalidOperationException();
            int epoch=(int)data["epoch"];
            if(value=="ready" && epoch==0 && gate.Phase=="starting") {
                gate.Ready();readyReply.TrySetResult(true);return;
            }
            if(value=="error") { Fault();return; }
            if(gate.Acknowledge(value,epoch)) {
                if(commandReply!=null && commandEpoch==epoch) commandReply.TrySetResult(true);
                Publish(value=="armed" ? "listening" : "waiting-for-idle");return;
            }
            if(value=="wake" && gate.Wake(epoch)) {
                turnHold=true;notBefore=DateTime.UtcNow.AddSeconds(2);Publish("handing-off");
                // Child emits wake only after stream.close(); no audio is forwarded.
                Dispatch(async () => { if(Enabled && turnHold && gate.Epoch==epoch && gate.Phase=="disarmed") await talk(); });
            }
        } catch { Fault(); }
    }
    void Fault() {
        gate.Disable();timer.Stop();
        if(readyReply!=null) readyReply.TrySetResult(false);
        if(commandReply!=null) commandReply.TrySetResult(false);
        bool stopped=StopChild();Publish(stopped ? "unavailable" : "release-uncertain");
    }
    async Task<bool> Command(bool arm) {
        await commandLock.WaitAsync();
        try { return await LockedCommand(arm); }
        finally { commandLock.Release(); }
    }
    async Task<bool> LockedCommand(bool arm) {
        if(arm && (!Enabled || turnHold)) return true;
        if(child==null || child.HasExited) return false;
        int epoch=gate.Command(arm);commandEpoch=epoch;
        var reply=new TaskCompletionSource<bool>();commandReply=reply;
        try {
            child.StandardInput.WriteLine(new JavaScriptSerializer().Serialize(new {command=arm ? "arm" : "disarm",epoch=epoch}));
            child.StandardInput.Flush();
            bool success=await Task.WhenAny(reply.Task,Task.Delay(3000))==reply.Task && await reply.Task;
            if(!success) Fault();
            return success;
        } catch { Fault();return false; }
        finally { if(commandReply==reply) commandReply=null; }
    }
    async Task Tick() {
        if(ticking || disposed || !Enabled) return;
        ticking=true;
        try {
            bool observed=await idle();
            bool safe=observed && !turnHold && DateTime.UtcNow>=notBefore;
            if(!Enabled) return;
            if(safe && gate.Phase=="disarmed") await Command(true);
            else if(!safe && (gate.Phase=="armed" || gate.Phase=="arming")) await Command(false);
        } catch { Fault(); }
        finally { ticking=false; }
    }
    public async Task<bool> BeforeTalkAsync() {
        turnHold=true;notBefore=DateTime.UtcNow.AddSeconds(2);
        if(child==null) return true;
        if(gate.Phase=="starting") { Fault();return StopChild(); }
        // Always request a fresh release acknowledgement, including a pending arm.
        if(await Command(false)) return true;
        return StopChild();
    }
    public void TurnStartedOrFailed() { turnHold=false;notBefore=DateTime.UtcNow.AddSeconds(2); }
    public void TurnClosed() { turnHold=false;notBefore=DateTime.UtcNow.AddSeconds(2); }
    public async Task DisableAsync() {
        timer.Stop();gate.Disable();turnHold=false;
        if(child!=null) await Command(false);
        bool stopped=StopChild();gate.Disable();Publish(stopped ? "off" : "release-uncertain");
    }
    bool StopChild() {
        var process=child;
        if(process==null) return true;
        try {
            if(!process.HasExited) {
                try { process.StandardInput.Close(); } catch { }
                if(!process.WaitForExit(500)) { process.Kill();if(!process.WaitForExit(1000)) return false; }
            }
            child=null;
            if(readyReply!=null) readyReply.TrySetResult(false);
            if(commandReply!=null) commandReply.TrySetResult(false);
            process.Dispose();return true;
        } catch { return false; }
    }
    public void Dispose() {
        if(disposed) return;
        disposed=true;gate.Disable();timer.Stop();timer.Dispose();StopChild();
    }
}

// Photo Curator — single-window dev launcher.
//
// One console window that owns BOTH dev servers as child processes:
//   - backend  : uvicorn (FastAPI) on :8077
//   - frontend : Vite on :5177
// Their output streams into THIS window. Closing the window (X / Ctrl+C / taskbar
// "Close window") terminates both servers, because every child is placed in a
// Windows Job Object created with KILL_ON_JOB_CLOSE.
//
// Built as an .exe with the app icon embedded (csc /win32icon:photo-curator.ico)
// so the taskbar shows the Photo Curator icon, it's directly pinnable, and the
// running window merges into the pinned button (same exe => same taskbar group).
//
// Build:  scripts\build-exe.ps1

using System;
using System.Diagnostics;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Threading;

static class PhotoCurator
{
    // Resolve the project root from where this executable actually lives (it is built into
    // scripts/, so the repo root is its parent), with an env-var override. Hard-coding one
    // machine's path meant the launcher only ever worked on the machine that built it.
    static readonly string BASE =
        Environment.GetEnvironmentVariable("PHOTO_CURATOR_HOME")
        ?? System.IO.Path.GetFullPath(System.IO.Path.Combine(
               AppDomain.CurrentDomain.BaseDirectory, ".."));

    // ---- Job Object interop: tie child processes' lifetime to this window ----
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    static extern IntPtr CreateJobObject(IntPtr a, string name);
    [DllImport("kernel32.dll")]
    static extern bool SetInformationJobObject(IntPtr job, int infoClass,
        ref JOBOBJECT_EXTENDED_LIMIT_INFORMATION info, int length);
    [DllImport("kernel32.dll")]
    static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
    [DllImport("kernel32.dll")]
    static extern IntPtr GetCurrentProcess();

    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_BASIC_LIMIT_INFORMATION
    {
        public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass, SchedulingClass;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct IO_COUNTERS
    {
        public ulong ReadOperationCount, WriteOperationCount, OtherOperationCount,
            ReadTransferCount, WriteTransferCount, OtherTransferCount;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit, JobMemoryLimit,
            PeakProcessMemoryUsed, PeakJobMemoryUsed;
    }
    const int JobObjectExtendedLimitInformation = 9;
    const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000;

    // Put THIS process in a kill-on-close job. Every descendant we spawn inherits
    // the job, so when this window dies the whole tree is force-terminated.
    static void BindLifetimeToWindow()
    {
        IntPtr job = CreateJobObject(IntPtr.Zero, null);
        if (job == IntPtr.Zero) return;
        var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        SetInformationJobObject(job, JobObjectExtendedLimitInformation,
            ref info, Marshal.SizeOf(info));
        AssignProcessToJobObject(job, GetCurrentProcess());
        // Deliberately leak the handle: it must stay open for the process's whole
        // life. When the process ends, the handle closes and the job kills children.
    }

    static Process StartChild(string file, string args, string workdir)
    {
        var psi = new ProcessStartInfo
        {
            FileName = file,
            Arguments = args,
            WorkingDirectory = workdir,
            UseShellExecute = false, // share this console -> output appears here
        };
        return Process.Start(psi);
    }

    static bool TryConnect(string host, int port)
    {
        try
        {
            using (var c = new TcpClient())
            {
                var ar = c.BeginConnect(host, port, null, null);
                return ar.AsyncWaitHandle.WaitOne(600) && c.Connected;
            }
        }
        catch { return false; }
    }

    // Probe both stacks: uvicorn listens on 127.0.0.1, but Vite binds [::1] on
    // Windows — checking only IPv4 would never see the frontend come up.
    static bool WaitForPort(int port, int timeoutSeconds)
    {
        for (int i = 0; i < timeoutSeconds; i++)
        {
            if (TryConnect("127.0.0.1", port) || TryConnect("::1", port)) return true;
            Thread.Sleep(600);
        }
        return false;
    }

    // Open the default browser via explorer.exe so the browser is spawned by the
    // shell, NOT as a child of this process. If we launched it directly it would
    // join our kill-on-close job and get closed when this window closes.
    static void OpenBrowser(string url)
    {
        try { Process.Start("explorer.exe", url); } catch { /* user can open manually */ }
    }

    static int Main()
    {
        Console.Title = "Photo Curator — backend :8077 + frontend :5177";
        BindLifetimeToWindow();

        string be = BASE + @"\backend";
        string fe = BASE + @"\frontend";
        string python = be + @"\.venv\Scripts\python.exe";
        string comspec = Environment.GetEnvironmentVariable("ComSpec"); // cmd.exe

        Console.WriteLine("Starting backend on :8077 ...");
        Process backend = StartChild(python,
            "-m uvicorn app.main:app --reload --port 8077", be);

        Console.WriteLine("Waiting for backend ...");
        Console.WriteLine(WaitForPort(8077, 30) ? "Backend ready."
            : "Backend slow to start — continuing anyway.");

        Console.WriteLine("Starting frontend on :5177 ...");
        // npm is npm.cmd — run it through cmd.exe so PATH resolution works.
        Process frontend = StartChild(comspec, "/c npm run dev", fe);

        bool feReady = WaitForPort(5177, 60);
        Console.WriteLine(feReady
            ? "Frontend ready — opening browser."
            : "Frontend slow — opening browser anyway.");
        if (feReady) Thread.Sleep(800);   // let Vite finish serving its first response
        OpenBrowser("http://localhost:5177");

        Console.WriteLine();
        Console.WriteLine("  Backend  ->  http://127.0.0.1:8077/docs");
        Console.WriteLine("  Frontend ->  http://localhost:5177");
        Console.WriteLine("  Close this window (or press Ctrl+C) to stop BOTH servers.");
        Console.WriteLine();

        // Block while the servers run. If either exits on its own (e.g. a crash),
        // fall through so the user can read the error before the window closes.
        WaitHandle.WaitAny(new WaitHandle[] {
            new ProcessWaitHandle(backend), new ProcessWaitHandle(frontend)
        });

        Console.WriteLine();
        Console.WriteLine("A server stopped. Press any key to close (this kills the other server).");
        try { Console.ReadKey(true); } catch { }
        return 0;
    }

    // Minimal WaitHandle wrapper around a process handle.
    sealed class ProcessWaitHandle : WaitHandle
    {
        public ProcessWaitHandle(Process p)
        {
            this.SafeWaitHandle =
                new Microsoft.Win32.SafeHandles.SafeWaitHandle(p.Handle, false);
        }
    }
}

using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace OfSfPortable {
    internal static class Entry {
        const string Product="OF_SF-Player-1.1.12", Marker=".of-player-patch.json", Index="player-files.json", Exe="OF_SF 登陆器.exe";
        static readonly JavaScriptSerializer Json=new JavaScriptSerializer();
        static string Hex(byte[] data){return BitConverter.ToString(data).Replace("-","").ToLowerInvariant();}
        static string Digest(Stream input){using(var hash=SHA256.Create())return Hex(hash.ComputeHash(input));}
        static string Read(ZipArchiveEntry entry){using(var reader=new StreamReader(entry.Open(),Encoding.UTF8))return reader.ReadToEnd();}
        static bool Owned(string root) {
            string path=Path.Combine(root,Marker);
            if(!File.Exists(path)||new FileInfo(path).Length>1024)return false;
            var doc=Json.Deserialize<Dictionary<string,string>>(File.ReadAllText(path));
            return doc.ContainsKey("product")&&doc["product"]==Product;
        }
        static void CheckOrdinary(string file) {
            if((File.Exists(file)||Directory.Exists(file))&&(File.GetAttributes(file)&FileAttributes.ReparsePoint)!=0)
                throw new IOException("工作目录含重定向链接，请把 EXE 放到独立的普通文件夹。");
        }
        static string Target(string root,string rel){
            var parts=rel.Split('/');
            if(parts.Any(s=>s==""||s=="."||s==".."||s.IndexOfAny(Path.GetInvalidFileNameChars())>=0))
                throw new InvalidDataException("安装清单含无效路径。");
            string path=root;foreach(var part in parts){path=Path.Combine(path,part);CheckOrdinary(path);}
            return path;
        }
        static void InstallFile(string path,ZipArchiveEntry member,string desired) {
            if(File.Exists(path)){using(var input=File.OpenRead(path))if(Digest(input)==desired)return;}
            var parent=Path.GetDirectoryName(path);Directory.CreateDirectory(parent);
            var pending=Path.Combine(parent,".of-install-"+Guid.NewGuid().ToString("N"));
            try {
                using(var raw=member.Open())using(var file=new FileStream(pending,FileMode.CreateNew,FileAccess.Write,FileShare.None))raw.CopyTo(file);
                using(var file=File.OpenRead(pending))if(Digest(file)!=desired)throw new InvalidDataException("安装文件校验失败。");
                if(File.Exists(path))File.Replace(pending,path,null);else File.Move(pending,path);
            } finally {if(File.Exists(pending))File.Delete(pending);}
        }
        static Dictionary<string,string> Manifest(string raw){
            var doc=Json.Deserialize<ManifestData>(raw);
            if(doc==null||doc.version!=1||doc.files==null)throw new InvalidDataException("安装清单无效。");
            return doc.files;
        }
        sealed class ManifestData { public int version {get;set;} public Dictionary<string,string> files {get;set;} }
        static void Install(string root){
            CheckOrdinary(root);Directory.CreateDirectory(root);
            string marker=Target(root,Marker),index=Target(root,Index);
            if(Directory.EnumerateFileSystemEntries(root).Any()&&!Owned(root))
                throw new IOException("同目录已有名为「OF_SF 登陆器」的其他文件夹。请把 EXE 放在另一位置。");
            using(var payload=Assembly.GetExecutingAssembly().GetManifestResourceStream("OF_SF.zip"))
            using(var archive=new ZipArchive(payload,ZipArchiveMode.Read)){
                var desired=Manifest(Read(archive.GetEntry(Index)));
                if(archive.Entries.Count!=desired.Count+2||!desired.ContainsKey(Exe))throw new InvalidDataException("安装包不完整。");
                var previous=File.Exists(index)?Manifest(File.ReadAllText(index)):new Dictionary<string,string>();
                var stamp=archive.GetEntry(Marker);string sha;using(var raw=stamp.Open())sha=Digest(raw);InstallFile(marker,stamp,sha);
                foreach(var item in desired){
                    var member=archive.GetEntry(item.Key);if(member==null)throw new InvalidDataException("安装包缺少文件。");
                    InstallFile(Target(root,item.Key),member,item.Value);
                }
                foreach(var item in previous.Where(p=>!desired.ContainsKey(p.Key))){
                    // Only recognized files retired from prior public packages are removed.
                    if(item.Key!="OF_SF 玩家补丁.exe"&&item.Key!="client_tools/realm_invitation.py"&&item.Key!="朋友开箱.md")continue;
                    var path=Target(root,item.Key);if(!File.Exists(path))continue;
                    using(var stream=File.OpenRead(path))if(Digest(stream)!=item.Value)continue;
                    File.Delete(path);
                }
                var list=archive.GetEntry(Index);using(var raw=list.Open())sha=Digest(raw);InstallFile(index,list,sha);
            }
        }
        static string Quote(string arg){
            var result=new StringBuilder("\"");int slashes=0;
            foreach(char c in arg){
                if(c=='\\'){slashes++;continue;}
                if(c=='\"'){result.Append('\\',2*slashes+1).Append(c);slashes=0;continue;}
                result.Append('\\',slashes).Append(c);slashes=0;
            }
            return result.Append('\\',2*slashes).Append('\"').ToString();
        }
        [STAThread] static int Main(string[] args){
            try{
                var root=Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory,"OF_SF 登陆器"));
                string lockName;using(var hash=SHA256.Create())lockName="Local\\OF_SF_Install_"+Hex(hash.ComputeHash(Encoding.UTF8.GetBytes(root.ToUpperInvariant())));
                using(var sync=new Mutex(false,lockName)){
                    bool taken=false;
                    try{
                        try{taken=sync.WaitOne(TimeSpan.FromSeconds(60));}catch(AbandonedMutexException){taken=true;}
                        if(!taken)throw new IOException("另一份登陆器正在准备文件，请稍后重试。");
                        Install(root);
                    }finally{if(taken)sync.ReleaseMutex();}
                }
                var exe=Path.Combine(root,Exe);using(var app=Process.Start(new ProcessStartInfo(exe,String.Join(" ",args.Select(Quote))){WorkingDirectory=root,UseShellExecute=false})){
                    if(args.Contains("--check-ui")||args.Contains("--render-preview")){app.WaitForExit();return app.ExitCode;}
                }return 0;
            }catch(Exception e){
                if(!args.Contains("--check-ui")&&!args.Contains("--render-preview"))MessageBox.Show(e.Message,"登陆器",MessageBoxButtons.OK,MessageBoxIcon.Error);
                return 2;
            }
        }
    }
}

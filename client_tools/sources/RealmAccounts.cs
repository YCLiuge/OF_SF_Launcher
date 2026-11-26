using System;
using System.Collections.Generic;
using System.Security.Cryptography;
using System.Text;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace OfSfPlayer {
    internal sealed class RealmAccounts {
        readonly string branch;
        internal RealmAccounts(string branch) { this.branch=branch; }

        internal static string Endpoint(string input) {
            string raw=(input??"").Trim();
            if(raw.Length==0||raw.IndexOfAny(new[]{'\\','@','?','#','%'})>=0)return "";
            if(!raw.Contains("://")){
                System.Net.IPAddress ip;
                if(!raw.StartsWith("[")&&System.Net.IPAddress.TryParse(raw,out ip)&&ip.AddressFamily==System.Net.Sockets.AddressFamily.InterNetworkV6)
                    raw="["+ip+"]";
                raw="https://"+raw;
            }
            Uri u;
            if(!Uri.TryCreate(raw,UriKind.Absolute,out u)||u.Scheme!="https"||u.AbsolutePath!="/"||u.Port<1)return "";
            string authority=raw.Substring(raw.IndexOf("://",StringComparison.Ordinal)+3).TrimEnd('/');
            bool explicitPort=authority.LastIndexOf(':')>authority.LastIndexOf(']');
            if(authority.EndsWith(":",StringComparison.Ordinal))return "";
            string host=u.IdnHost.ToLowerInvariant();
            if(u.HostNameType==UriHostNameType.IPv6)host="["+host.Trim('[',']')+"]";
            return "https://"+host+":"+(explicitPort?u.Port:18443);
        }

        static byte[] Entropy(string realm) {
            using(var hash=SHA256.Create())return hash.ComputeHash(Encoding.UTF8.GetBytes("OF_SF account\n"+realm));
        }
        static string Label(byte[] entropy){return BitConverter.ToString(entropy).Replace("-","");}
        internal string[] Read(string realm) {
            if(realm=="")return new[]{"",""};
            var entropy=Entropy(realm);
            using(var key=Registry.CurrentUser.OpenSubKey(branch)){
                var cipher=key==null?null:key.GetValue(Label(entropy)) as byte[];
                if(cipher==null)return new[]{"",""};
                try {
                    var bytes=ProtectedData.Unprotect(cipher,entropy,DataProtectionScope.CurrentUser);
                    try {
                        var fields=new JavaScriptSerializer().Deserialize<string[]>(Encoding.UTF8.GetString(bytes));
                        if(fields!=null&&fields.Length==2)return fields;
                    } finally {Array.Clear(bytes,0,bytes.Length);}
                }catch(CryptographicException){}catch(ArgumentException){}catch(InvalidOperationException){}
                return new[]{"",""};
            }
        }
        internal void Save(string realm,string username,string secret) {
            if(realm==""||username=="")return;
            var entropy=Entropy(realm);
            var bytes=Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(new[]{username,secret}));
            try{
                var cipher=ProtectedData.Protect(bytes,entropy,DataProtectionScope.CurrentUser);
                using(var key=Registry.CurrentUser.CreateSubKey(branch))key.SetValue(Label(entropy),cipher,RegistryValueKind.Binary);
            } finally {Array.Clear(bytes,0,bytes.Length);}
        }
        internal void ForgetPassword(string realm){
            var saved=Read(realm);
            if(saved[1]!="")Save(realm,saved[0],"");
        }
    }
}

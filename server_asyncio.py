#!/usr/bin/env python3
from pathlib import Path
import socket
import aiohttp
import aiohttp_socks
import asyncio
import time
import random
import copy
import json
import sys
import ahocorasick
import dns.message   #  --> pip install dnspython
import dns.rdatatype
import base64


listen_PORT = 2500    # pyprox listening to 127.0.0.1:listen_PORT
DOH_PORT = 2500

log_every_N_sec = 30   # every 30 second , update log file with latest DNS-cache statistics

allow_insecure = True   # set true to allow certificate domain mismatch in DoH
my_socket_timeout = 120 # default for google is ~21 sec , recommend 60 sec unless you have low ram and need close soon
first_time_sleep = 0.1 # speed control , avoid server crash if huge number of users flooding
accept_time_sleep = 0.01 # avoid server crash on flooding request -> max 100 sockets per second
output_data=True



domain_settings={
    "null": {
        "IP": "127.0.0.1",
        "TCP_frag": 114514,
        "TCP_sleep": 0.001,
        "TLS_frag": 114514,
        "num_TCP_fragment": 37,
        "num_TLS_fragment": 37,
    }
}

num_TCP_fragment = 37
num_TLS_fragment = 37
TCP_sleep = 0.001
TCP_frag=0
TLS_frag=0
IPtype="ipv4"
doh_server="https://127.0.0.1/dns-query"
DNS_log_every=1

domain_settings=None
domain_settings_tree=None


DNS_cache = {}      # resolved domains
IP_DL_traffic = {}  # download usage for each ip
IP_UL_traffic = {}  # upload usage for each ip

with open("config.json",'r', encoding='UTF-8') as f:
    config = json.load(f)
    output_data=config.get("output_data")

    my_socket_timeout=config.get("my_socket_timeout")
    listen_PORT=config.get("listen_PORT")
    DOH_PORT=config.get("DOH_PORT")
    
    num_TCP_fragment=config.get("num_TCP_fragment")
    num_TLS_fragment=config.get("num_TLS_fragment")
    TCP_sleep=config.get("TCP_sleep")
    TCP_frag=config.get("TCP_frag")
    TLS_frag=config.get("TLS_frag")
    doh_server=config.get("doh_server")
    domain_settings=config.get("domains")
    DNS_log_every=config.get("DNS_log_every")
    IPtype=config.get("IPtype")

    # print(set(domain_settings.keys()))
    domain_settings_tree=ahocorasick.AhoCorasick(*domain_settings.keys())

try:
    with open("DNS_cache.json",'r+', encoding='UTF-8') as f:
        DNS_cache=json.load(f)
except Exception as e:
    print("ERROR DNS query: ",repr(e))

cnt_chg = 0

class GET_settings:
    def __init__(self):
        self.url = doh_server
        self.req = None  
        self.connector=None  
        self.knocker_proxy = {
        'https': 'http://127.0.0.1:'+str(DOH_PORT)
        }
        
    async def init_session(self):
        self.connector = aiohttp_socks.ProxyConnector.from_url(self.knocker_proxy["https"])
        self.req = aiohttp.ClientSession(connector=self.connector)


    async def query_DNS(self,server_name,settings):     
        quary_params = {
            # 'name': server_name,    # no need for this when using dns wire-format , cause 400 err on some server
            'type': 'A',
            'ct': 'application/dns-message',
            }
        if settings["IPtype"]=="ipv6":
            quary_params['type']="AAAA";
        else:
            quary_params['type']="A";

        print(f'online DNS Query',server_name)        
        try:
            if settings["IPtype"]=="ipv6":
                query_message = dns.message.make_query(server_name,'AAAA')
            else:
                query_message = dns.message.make_query(server_name,'A')
            query_wire = query_message.to_wire()
            query_base64 = base64.urlsafe_b64encode(query_wire).decode('utf-8')
            query_base64 = query_base64.replace('=','')    # remove base64 padding to append in url            

            query_url = self.url + query_base64


            ans = await self.req.get( query_url , params=quary_params , headers={'accept': 'application/dns-message'},proxy="http://www.baidu.com:1024")
            # print("ans1: ",ans)
            # print(ans.content)
            anscontent=await ans.content.read()
            
            
            # Parse the response as a DNS packet
            if ans.status == 200 and ans.headers.get('content-type') == 'application/dns-message':
                answer_msg = dns.message.from_wire(anscontent)

                resolved_ip = None
                for x in answer_msg.answer:
                    if ((settings["IPtype"] == "ipv6" and x.rdtype == dns.rdatatype.AAAA) or (settings["IPtype"] == "ipv4" and x.rdtype == dns.rdatatype.A)):
                        resolved_ip = x[0].address    # pick first ip in DNS answer
                        try:
                            if settings.get("IPcache")==False:
                                pass
                            else:
                                DNS_cache[server_name] = resolved_ip                        
                        except:    
                            DNS_cache[server_name] = resolved_ip                        
                        # print("################# DNS Cache is : ####################")
                        # print(DNS_cache)         # print DNS cache , it usefull to track all resolved IPs , to be used later.
                        # print("#####################################################")
                        break
                
                print(f'online DNS --> Resolved {server_name} to {resolved_ip}')                
                return resolved_ip
            else:
                print(f'Error DNS query: {ans.status} {ans.reason}')
                raise Exception("Error DNS query: "+str(ans.status))
        except Exception as e:
            print("ERROR DNS query: ",repr(e))
            raise e


    async def query(self,domain,todns=True):
        # print("Query:",domain)
        res=domain_settings_tree.search(domain)
        # print(domain,'-->',sorted(res,key=lambda x:len(x),reverse=True)[0])
        try:
            res=copy.deepcopy(domain_settings.get(sorted(res,key=lambda x:len(x),reverse=True)[0]))
        except:
            res={}
        
        if todns:
          if res.get("IPtype")==None:
              res["IPtype"]=IPtype
  
          if res.get("IP")==None:
              if DNS_cache.get(domain)!=None:
                  res["IP"]=DNS_cache[domain]
              else:
                  res["IP"]=await self.query_DNS(domain,res)
                  if res["IP"]==None:
                      print("Faild to resolve domain, try again with other IP type")
                      if res["IPtype"]=="ipv6":                        
                          res["IPtype"]="ipv4"
                      elif res["IPtype"]=="ipv4":
                          res["IPtype"]="ipv6"
                      res["IP"]=await self.query_DNS(domain,res)
                  global cnt_chg
                  cnt_chg=cnt_chg+1
                  if cnt_chg>DNS_log_every:
                      cnt_chg=0
                      with open("DNS_cache.json",'w', encoding='UTF-8') as f:
                          json.dump(DNS_cache,f)
              # res["IP"]="127.0.0.1"
        if res.get("TCP_frag")==None:
            res["TCP_frag"]=TCP_frag
        if res.get("TCP_sleep")==None:
            res["TCP_sleep"]=TCP_sleep
        if res.get("TLS_frag")==None:
            res["TLS_frag"]=TLS_frag
        if res.get("num_TCP_fragment")==None:
            res["num_TCP_fragment"]=num_TCP_fragment
        if res.get("num_TLS_fragment")==None:
            res["num_TLS_fragment"]=num_TLS_fragment
        print(domain,'-->',res)
        return res
    


class AsyncServer(object):
    def __init__(self, host, port):
        self.DoH=GET_settings()
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.host, self.port))

    async def listen(self):
        await self.DoH.init_session()
        self.sock.setblocking(False)
        self.sock.listen(128)  # up to 128 concurrent unaccepted socket queued , the more is refused untill accepting those.
                        
        while True:
            client_sock , client_addr = await asyncio.get_running_loop().sock_accept(self.sock)                    
            client_sock , client_addr = await asyncio.get_running_loop().sock_accept(self.sock)                    
            client_sock.setblocking(False)
                        
            asyncio.create_task(self.my_upstream(client_sock))
            await asyncio.sleep(0)
    


    async def handle_client_request(self,client_socket):
        # Receive the CONNECT request from the client
        data = await asyncio.get_running_loop().sock_recv(client_socket,16384)
        

        if(data[:7]==b'CONNECT'):            
            server_name , server_port = self.extract_servername_and_port(data)            
        elif( (data[:3]==b'GET') 
            or (data[:4]==b'POST') 
            or (data[:4]==b'HEAD')
            or (data[:7]==b'OPTIONS')
            or (data[:3]==b'PUT') 
            or (data[:6]==b'DELETE') 
            or (data[:5]==b'PATCH') 
            or (data[:5]==b'TRACE') ):  

            q_line = str(data).split('\r\n')
            q_req = q_line[0].split()
            q_method = q_req[0]
            q_url = q_req[1]
            q_url = q_url.replace('http://','https://')
            print('************************@@@@@@@@@@@@***************************')
            print('redirect',q_method,'http to HTTPS',q_url)          
            response_data = 'HTTP/1.1 302 Found\r\nLocation: '+q_url+'\r\nProxy-agent: MyProxy/1.0\r\n\r\n'            
            await asyncio.get_running_loop().sock_sendall(client_socket,response_data.encode())
            client_socket.close()            
            return None, {}
        else:
            print('Unknown Method',str(data[:10]))            
            response_data = b'HTTP/1.1 400 Bad Request\r\nProxy-agent: MyProxy/1.0\r\n\r\n'
            await asyncio.get_running_loop().sock_sendall(client_socket,response_data)
            client_socket.close()            
            return None, {}

        
        print(server_name,'-->',server_port)
        

        try:
            try:
                socket.inet_aton(server_name)
                server_IP = server_name
                settings={}
                
            except socket.error:
                # print('Not IP , its domain , try to resolve it')
                try:
                    settings=await self.DoH.query(server_name)
                except Exception as e:
                    raise e
                if settings==None:                    
                    settings={}
                settings["sni"]=bytes(server_name,encoding="utf-8")
                # print("hdxl",settings)
                server_IP=settings.get("IP")
                if settings.get("port"):
                    server_port=settings.get("port")
                print("send to ",server_IP,":",server_port)
                
            if server_IP.find(":")==-1:
                server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            else:
                server_socket = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
            
            server_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   #force localhost kernel to send TCP packet immediately (idea: @free_the_internet)
            server_socket.setblocking(False)
            
            try:
                try:
                    await asyncio.wait_for(asyncio.get_running_loop().sock_connect(server_socket,(server_IP, server_port)),my_socket_timeout)
                except Exception:
                    await asyncio.wait_for(asyncio.get_running_loop().sock_connect(server_socket,(server_IP, server_port)),my_socket_timeout)
                # server_socket.connect((server_IP, server_port))
                # Send HTTP 200 OK
                response_data = b'HTTP/1.1 200 Connection established\r\nProxy-agent: MyProxy/1.0\r\n\r\n'            
                await asyncio.get_running_loop().sock_sendall(client_socket,response_data)
                return server_socket, settings
            except socket.error:
                print("@@@ "+server_IP+":"+str(server_port)+ " ==> filtered @@@")
                import traceback
                traceback.print_exc()
                # Send HTTP ERR 502
                response_data = b'HTTP/1.1 502 Bad Gateway (is IP filtered?)\r\nProxy-agent: MyProxy/1.0\r\n\r\n'
                await asyncio.get_running_loop().sock_sendall(client_socket,response_data)
                client_socket.close()
                server_socket.close()
                return server_IP, {}

            
        except Exception as e:
            print(repr(e))
            # Send HTTP ERR 502
            response_data = b'HTTP/1.1 502 Bad Gateway (Strange ERR?)\r\nProxy-agent: MyProxy/1.0\r\n\r\n'
            await asyncio.get_running_loop().sock_sendall(client_socket,response_data)
            client_socket.close()
            server_socket.close()
            return None, {}







    async def my_upstream(self, client_sock):
        first_flag = True
        
        backend_sock, settings = await self.handle_client_request(client_sock)
        # print("upst",settings)
        
        if(backend_sock==None):
            client_sock.close()
            return False
        
        if( isinstance(backend_sock,str) ):
            this_ip = backend_sock
            if(this_ip not in IP_UL_traffic):
                IP_UL_traffic[this_ip] = 0
                IP_DL_traffic[this_ip] = 0
            client_sock.close()
            return False

        
        this_ip = backend_sock.getpeername()[0]
        if(this_ip not in IP_UL_traffic):
            IP_UL_traffic[this_ip] = 0
            IP_DL_traffic[this_ip] = 0
        
        
        while True:
            try:
                if( first_flag == True ):                        
                    first_flag = False

                    
                    data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(client_sock,16384),my_socket_timeout)
                    #print('len data -> ',str(len(data)))                
                    #print('user talk :')

                    if data:                                                                                            
                        asyncio.create_task(self.my_downstream(backend_sock , client_sock, settings))
                        
                        # print(data)
                        try:
                            if settings.get("sni")==None:
                              print("No sni? try to dig it in packet like gfwm ")
                              settings["sni"]=parse_client_hello(data)
                              if settings["sni"]:
                                  settings=await self.DoH.query(str(settings.get("sni")),todns=False)
                        except Exception as e:
                          print(e)
                          import traceback
                          traceback_info = traceback.format_exc()
                          print(traceback_info)
                        await send_data_in_fragment(settings.get("sni"),settings,data,backend_sock)
                        IP_UL_traffic[this_ip] = IP_UL_traffic[this_ip] + len(data)

                    else:                   
                        raise Exception('cli syn close')

                else:
                    data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(client_sock,16384),my_socket_timeout)   
                    if data:
                        await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(backend_sock,data),my_socket_timeout)
                        IP_UL_traffic[this_ip] = IP_UL_traffic[this_ip] + len(data)                      
                    else:
                        raise Exception('cli pipe close')
                    
            except Exception as e:
                print('upstream : '+ repr(e) + 'from' , settings.get("sni") )
                
                client_sock.close()
                backend_sock.close()
                return False



            
    async def my_downstream(self, backend_sock , client_sock, settings):
        this_ip = backend_sock.getpeername()[0]        

        first_flag = True
        while True:
            try:
                if( first_flag == True ):
                    first_flag = False            
                    data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(backend_sock,16384),my_socket_timeout)
                    if data:
                        await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(client_sock,data),my_socket_timeout)
                        IP_DL_traffic[this_ip] = IP_DL_traffic[this_ip] + len(data)
                    else:
                        raise Exception('backend pipe close at first')
                    
                else:
                    data = await asyncio.wait_for(asyncio.get_running_loop().sock_recv(backend_sock,16384), my_socket_timeout)
                    if data:
                        await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(client_sock,data),my_socket_timeout)
                        IP_DL_traffic[this_ip] = IP_DL_traffic[this_ip] + len(data)
                    else:
                        raise Exception('backend pipe close')
            
            except Exception as e:
                print('downstream '+' : '+ repr(e) , settings.get("sni")) 
                
                backend_sock.close()
                client_sock.close()
                return False



    def extract_servername_and_port(self,data):        
        host_and_port = str(data).split()[1]
        host,port = host_and_port.split(':')
        return (host,int(port)) 
        
    import struct


def parse_client_hello(data):
  import struct
  # print(struct.calcsize(">BHH"))
  # 解析TLS记录
  content_type, version_major, version_minor, length = struct.unpack(">BBBH", data[:5])
  if content_type!= 0x16:  # 0x16表示TLS Handshake
      raise ValueError("Not a TLS Handshake message")
  handshake_data = data[5:5 + length]

  # 解析握手消息头
  handshake_type, tmp, length = struct.unpack(">BBH", handshake_data[:4])
  length=tmp*64+length
  if handshake_type!= 0x01:  # 0x01表示Client Hello
      raise ValueError("Not a Client Hello message")
  client_hello_data = handshake_data[4:4 + length]

  # 解析Client Hello消息
  client_version_major, client_version_minor, random_bytes, session_id_length = struct.unpack(">BB32sB", client_hello_data[:35])
  session_id = client_hello_data[35:35 + session_id_length]
  # print(client_hello_data[35 + session_id_length:35 + session_id_length + 2])
  cipher_suites_length = struct.unpack(">H", client_hello_data[35 + session_id_length:35 + session_id_length + 2])[0]
  cipher_suites = client_hello_data[35 + session_id_length + 2:35 + session_id_length + 2 + cipher_suites_length]
  compression_methods_length = struct.unpack(">B", client_hello_data[35 + session_id_length + 2 + cipher_suites_length:35 + session_id_length + 2 + cipher_suites_length + 1])[0]
  compression_methods = client_hello_data[35 + session_id_length + 2 + cipher_suites_length + 1:35 + session_id_length + 2 + cipher_suites_length + 1 + compression_methods_length]

  # 定位扩展部分
  extensions_offset = 35 + session_id_length + 2 + cipher_suites_length + 1 + compression_methods_length
  extensions_length = struct.unpack(">H", client_hello_data[extensions_offset:extensions_offset + 2])[0]
  extensions_data = client_hello_data[extensions_offset + 2:extensions_offset + 2 + extensions_length]

  offset = 0
  while offset < extensions_length:
      extension_type, extension_length = struct.unpack(">HH", extensions_data[offset:offset + 4])
      if extension_type == 0x0000:  # SNI扩展的类型是0x0000
          sni_extension = extensions_data[offset + 4:offset + 4 + extension_length]
          # 解析SNI扩展
          list_length = struct.unpack(">H", sni_extension[:2])[0]
          if list_length!= 0:
              name_type, name_length = struct.unpack(">BH", sni_extension[2:5])
              if name_type == 0:  # 域名类型
                  sni = sni_extension[5:5 + name_length]
                  return sni
      offset += 4 + extension_length
  return None




async def split_other_data(data, num_fragment, split):
    # print("sending: ", data)
    L_data = len(data)

    try:
        indices = random.sample(range(1,L_data-1), min(num_fragment,L_data-2))
    except:
        await split(data)
        return 0
    indices.sort()
    # print('indices=',indices)

    i_pre=0
    for i in indices:
        fragment_data = data[i_pre:i]
        i_pre=i
        # sock.send(fragment_data)
        # print(fragment_data)
        await split(new_frag=fragment_data)
        
    fragment_data = data[i_pre:L_data]
    await split(fragment_data)

    return 1
# http114=b""

async def split_data(data, sni, L_snifrag, num_fragment,split):
    stt=data.find(sni)
    if output_data:
        print(sni,stt)
    else:
        print("start of sni:",stt)

    if stt==-1:
        await split_other_data(data, num_fragment, split)
        return 0,0

    L_sni=len(sni)
    L_data=len(data)

    if L_snifrag==0:
        await split_other_data(data, num_fragment, split)
        return 0,0

    nstt=stt

    if await split_other_data(data[0:stt+L_snifrag], num_fragment, split):
         nstt=nstt+num_fragment*5
    
    nst=L_snifrag

    while nst<=L_sni:
        fragment_data=data[stt+nst:stt+nst+L_snifrag]
        await split(fragment_data)
        nst=nst+L_snifrag

    fraged_sni=data[stt:stt+nst]

    if await split_other_data(data[stt+nst:L_data], num_fragment, split):
          nstt=nstt+num_fragment*5

    return nstt,int(nstt+nst+nst*5/L_snifrag)

async def send_data_in_fragment(sni, settings, data , sock):
    # print(sni,settings)
    print("To send: ",len(data)," Bytes. ")
    if output_data:
        print("sending:    ",data,"\n")
    base_header = data[:3]
    record=data[5:]
    TLS_ans=b""
    async def TLS_add_frag(new_frag):
        nonlocal TLS_ans,base_header
        TLS_ans+=base_header + int.to_bytes(len(new_frag), byteorder='big', length=2)
        TLS_ans+=new_frag
        print("adding frag:",len(new_frag)," bytes. ")
        if output_data:
            print("adding frag: ",new_frag,"\n")
    stsni,edsni=await split_data(record, sni, settings.get("TLS_frag"), settings.get("num_TLS_fragment"),TLS_add_frag)
    if edsni>0:
        first_sni_frag=TLS_ans[stsni:edsni]
    else: 
        first_sni_frag=b''

    print("TLS fraged: ",len(TLS_ans)," Bytes. ")
    if output_data:
        print("TLS fraged: ",TLS_ans,"\n")

    T_sleep=settings.get("TCP_sleep")
    async def TCP_send_with_sleep(new_frag):
        nonlocal sock,T_sleep
        await asyncio.wait_for(asyncio.get_running_loop().sock_sendall(sock,new_frag),my_socket_timeout)
        print("TCP send: ",len(new_frag)," bytes. And 'll sleep for ",T_sleep, "seconds. ")
        if output_data:
            print("TCP send: ",new_frag,"\n")
        await asyncio.sleep(T_sleep)
    await split_data(TLS_ans, first_sni_frag, settings.get("TCP_frag"), settings.get("num_TCP_fragment"),TCP_send_with_sleep)
    
    print("----------finish------------")

def start_server():
    print ("Now listening at: 127.0.0.1:"+str(listen_PORT))
    try:
        # 检查是否有 --logfile 参数
        if "--logfile" in sys.argv:
            # 打开 log.txt 文件，使用 'w' 模式表示写入，如果文件不存在则创建，如果存在则覆盖
            sys.stdout = open('log.txt', 'w+')
    except Exception as e:
        print(f"An error occurred: {e}")
    asyncio.run(AsyncServer('',listen_PORT).listen())

if (__name__ == "__main__"):
    start_server()

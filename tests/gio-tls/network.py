"""Run real TLS 1.3/GIO interoperability; server failures are test failures."""
from pathlib import Path
import socket,ssl,subprocess,threading,os,time
import argparse
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--user-source',type=Path,required=True)
parser.add_argument('--client',type=Path,required=True)
args=parser.parse_args();fixtures=args.user_source.resolve()/'crates/tls-client/test-fixtures'
def run(mode):
 context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_3
 context.load_cert_chain(fixtures/'server.cert.pem',fixtures/'server.key.pem');context.set_alpn_protocols(['http/1.1'])
 listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();listener.settimeout(5);errors=[]
 def server():
  try:
   raw,_=listener.accept();raw.settimeout(5)
   if mode=='cancel':
    with raw:
     raw.recv(4096);time.sleep(.15)
    return
   try:stream=context.wrap_socket(raw,server_side=True)
   except ssl.SSLError:
    if mode in ('wrong-host','relaxed'):return
    raise
   with stream:
    assert mode not in ('wrong-host','relaxed'), 'Handshake unexpectedly accepted'
    request=b''
    while b'\r\n\r\n' not in request:request+=stream.recv(4096)
    assert request==b'GET / HTTP/1.1\r\nHost: tls.test.mochios\r\n\r\n'
    stream.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 1024\r\n\r\n'+b'x'*1024)
    stream.unwrap().close() # Require an authenticated client close_notify.
  except Exception as e:errors.append(e)
 thread=threading.Thread(target=server,daemon=True);thread.start()
 env=dict(os.environ,GIO_USE_TLS='mochios',G_DEBUG='fatal-warnings')
 try:result=subprocess.run([str(args.client.resolve()),str(listener.getsockname()[1]),mode],env=env,timeout=8)
 finally:thread.join(6);listener.close()
 if result.returncode or errors or thread.is_alive():raise RuntimeError((mode,result.returncode,errors,thread.is_alive()))
 print(mode+': PASS',flush=True)
for mode in ['sync','async','wrong-host','relaxed','cancel']:run(mode)

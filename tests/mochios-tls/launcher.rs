use mochi_user_platform as p;
use mochios_posix_client::{Client, SyscallEndpointTransport};
fn main() {
    println!("NATIVE-TLS SCOPED LAUNCHER");
    let posix = p::handle::inherited(mochios_posix_protocol::CONTROL_HANDLE_KEY).unwrap();
    let client = Client::new(SyscallEndpointTransport::new(posix));
    let scope = client.create_read_scope().unwrap();
    let service = u32::try_from(p::process::find_by_name("capability.service").unwrap()).unwrap();
    let mut request = vec![0; 24];
    request[..4].copy_from_slice(&0x4150_5053u32.to_le_bytes());
    for item in ["/system/bin/sdk-tls-smoke", "__MNU_EXEC_ENV=LD_LIBRARY_PATH=/system/libraries/sdk-tls-smoke"] {
        request.extend_from_slice(item.as_bytes()); request.push(0);
    }
    let mut handles = p::ipc::IpcObjectHandles::default();
    handles.count = 2;
    handles.handles[0].handle = posix;
    handles.handles[0].rights = p::handle::HANDLE_RIGHT_SEND | p::handle::HANDLE_RIGHT_DUPLICATE | p::handle::HANDLE_RIGHT_TRANSFER;
    handles.handles[1].handle = scope.handle;
    handles.handles[1].rights = mochios_posix_protocol::read_scope::ATTACH_RIGHTS;
    let mut reply = [0; 16];
    let result = p::ipc::call_object_handles(service, &request, &mut reply, &mut handles);
    p::handle::close(scope.handle).unwrap();
    let length = result.unwrap() as u32 as usize;
    assert_eq!(length, reply.len()); assert_eq!(handles.count, 0);
    let status = i64::from_le_bytes(reply[..8].try_into().unwrap());
    let pid = u64::from_le_bytes(reply[8..].try_into().unwrap());
    println!("NATIVE-TLS SCOPED LAUNCH status={status} pid={pid}");
    assert_eq!(status, 0); assert_ne!(pid, 0);
}

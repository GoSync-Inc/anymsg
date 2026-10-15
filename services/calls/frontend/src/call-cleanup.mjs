const activeStatuses=new Set(['waiting_for_browser','connecting','active']);

export async function settleDisconnectedCall(api,id) {
  const record=await api(`/api/calls/${id}`);
  // A peer callback may precede the server's disconnect callback, or the
  // initial offer may have failed without creating a server peer at all.
  return activeStatuses.has(record.status) ? api(`/api/calls/${id}/stop`,'POST') : record;
}

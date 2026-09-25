// Actions that read the document run only after its latest editor state reaches the server.
export async function gateSavedAction(coordinator, action) {
  if (!await coordinator.flush()) return false
  await action(coordinator.version)
  return true
}

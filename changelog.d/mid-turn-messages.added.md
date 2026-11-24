- **Add to a running turn.** In topics and chats, a message sent while the reply is
  still streaming no longer waits or needs **Stop**: it is queued, and the model reads
  it at its next step (after the current tool call, or once its answer is written, then
  carries on in the same turn). A message the turn never read goes back into the
  composer.

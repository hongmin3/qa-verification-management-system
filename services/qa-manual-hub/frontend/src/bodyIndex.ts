import type { BodyIndexResult } from './types'

// A stop request completes the current small batch, retaining its resume cursor.
export async function runIndexBatches(
  request: (cursor: string | null) => Promise<BodyIndexResult>,
  stopped: () => boolean,
  accepted: (batch: BodyIndexResult) => void,
  cursor: string | null,
): Promise<void> {
  while (!stopped()) {
    const batch = await request(cursor)
    accepted(batch)
    if (batch.done) return
    if (!batch.next_cursor || batch.next_cursor === cursor) {
      throw new Error('색인 진행 위치가 바뀌지 않았습니다. 서버 상태를 확인한 뒤 재개하세요.')
    }
    cursor = batch.next_cursor
  }
}

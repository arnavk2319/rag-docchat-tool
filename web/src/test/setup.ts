import '@testing-library/jest-dom/vitest'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { resetMockState } from '../mocks/handlers'
import { server } from '../mocks/server'

beforeAll(() => server.listen())
afterEach(() => {
  server.resetHandlers()
  resetMockState()
  sessionStorage.clear()
})
afterAll(() => server.close())

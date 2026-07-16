import { useQuery } from '@tanstack/react-query'
import { api } from './lib/api'
import './App.css'

type Health = { status: string }

/**
 * Placeholder shell. Its only job today is to prove the frontend can reach the
 * backend across origins (i.e. CORS is configured) before any real UI exists.
 */
function App() {
  const { data, isPending, error } = useQuery({
    queryKey: ['health'],
    queryFn: () => api.get<Health>('/health'),
  })

  const backend = isPending
    ? 'checking…'
    : error
      ? `unreachable — ${error.message}`
      : (data?.status ?? '?')

  return (
    <main>
      <h1>CP Upsolving Mentor</h1>
      <p>Backend: {backend}</p>
    </main>
  )
}

export default App

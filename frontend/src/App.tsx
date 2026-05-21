import './App.css'
import { MoodChart } from './components/MoodChart'
import { SearchPanel } from './components/SearchPanel'
import { StreamingPanel } from './components/StreamingPanel'

function App() {
  return (
    <div className="min-h-screen bg-gray-50">
      <header className="border-b border-gray-200 bg-white px-6 py-4">
        <h1 className="text-2xl font-bold text-gray-900">Journal Analytics</h1>
        <p className="text-sm text-gray-500">Patterns from your personal journal</p>
      </header>

      <main className="mx-auto max-w-7xl px-6 py-8">
        <div className="mb-8 rounded-xl bg-white p-6 shadow-sm">
          <MoodChart />
        </div>

        <div className="grid grid-cols-1 gap-8 lg:grid-cols-2">
          <div className="rounded-xl bg-white p-6 shadow-sm">
            <StreamingPanel />
          </div>
          <div className="rounded-xl bg-white p-6 shadow-sm">
            <SearchPanel />
          </div>
        </div>
      </main>
    </div>
  )
}

export default App

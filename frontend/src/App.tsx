import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ProtectedRoute } from './components/ProtectedRoute'
import { AccountPage } from './pages/AccountPage'
import { DashboardPage } from './pages/DashboardPage'
import { IngestPage } from './pages/IngestPage'
import { LoginPage } from './pages/LoginPage'
import { ProblemPage } from './pages/ProblemPage'
import { RegisterPage } from './pages/RegisterPage'
import { TutorPage } from './pages/TutorPage'
import './App.css'

function Protected({ children }: { children: React.ReactNode }) {
  return (
    <ProtectedRoute>
      <Layout>{children}</Layout>
    </ProtectedRoute>
  )
}

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/"
          element={
            <Protected>
              <DashboardPage />
            </Protected>
          }
        />
        <Route
          path="/ingest"
          element={
            <Protected>
              <IngestPage />
            </Protected>
          }
        />
        <Route
          path="/problems/:slug"
          element={
            <Protected>
              <ProblemPage />
            </Protected>
          }
        />
        <Route
          path="/problems/:slug/tutor"
          element={
            <Protected>
              <TutorPage />
            </Protected>
          }
        />
        <Route
          path="/account"
          element={
            <Protected>
              <AccountPage />
            </Protected>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}

export default App

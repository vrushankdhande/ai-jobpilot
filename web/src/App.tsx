import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import { FlashProvider, Loading } from "./components/ui";

const Home = lazy(() => import("./pages/Home"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const JobDiscovery = lazy(() => import("./pages/JobDiscovery"));
const ResumeManager = lazy(() => import("./pages/ResumeManager"));
const JobMatching = lazy(() => import("./pages/JobMatching"));
const DocumentGeneration = lazy(() => import("./pages/DocumentGeneration"));
const ApplicationQueue = lazy(() => import("./pages/ApplicationQueue"));
const AppliedJobs = lazy(() => import("./pages/AppliedJobs"));
const InterviewTracker = lazy(() => import("./pages/InterviewTracker"));
const Analytics = lazy(() => import("./pages/Analytics"));
const Automation = lazy(() => import("./pages/Automation"));
const Settings = lazy(() => import("./pages/Settings"));

export default function App() {
  return (
    <FlashProvider>
      <Suspense fallback={<Loading />}>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="dashboard" element={<Dashboard />} />
            <Route path="job-discovery" element={<JobDiscovery />} />
            <Route path="resume-manager" element={<ResumeManager />} />
            <Route path="job-matching" element={<JobMatching />} />
            <Route path="document-generation" element={<DocumentGeneration />} />
            <Route path="application-queue" element={<ApplicationQueue />} />
            <Route path="applied-jobs" element={<AppliedJobs />} />
            <Route path="interview-tracker" element={<InterviewTracker />} />
            <Route path="analytics" element={<Analytics />} />
            <Route path="automation" element={<Automation />} />
            <Route path="settings" element={<Settings />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </Suspense>
    </FlashProvider>
  );
}

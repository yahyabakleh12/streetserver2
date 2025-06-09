-- phpMyAdmin SQL Dump
-- version 5.2.1
-- https://www.phpmyadmin.net/
--
-- Host: 127.0.0.1
-- Generation Time: Jun 09, 2025 at 08:16 AM
-- Server version: 10.4.32-MariaDB
-- PHP Version: 8.2.12

SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";


/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET @OLD_CHARACTER_SET_RESULTS=@@CHARACTER_SET_RESULTS */;
/*!40101 SET @OLD_COLLATION_CONNECTION=@@COLLATION_CONNECTION */;
/*!40101 SET NAMES utf8mb4 */;

--
-- Database: `parking_management`
--

-- --------------------------------------------------------

--
-- Table structure for table `cameras`
--

CREATE TABLE `cameras` (
  `id` int(11) NOT NULL,
  `pole_id` int(11) NOT NULL,
  `api_code` varchar(100) NOT NULL,
  `p_ip` varchar(45) NOT NULL,
  `number_of_parking` int(11) DEFAULT 0,
  `vpn_ip` varchar(45) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `cameras`
--

INSERT INTO `cameras` (`id`, `pole_id`, `api_code`, `p_ip`, `number_of_parking`, `vpn_ip`) VALUES
(1, 1, '95', '192.168.169.95', 6, '10.0.0.11'),
(4, 1, '56', '192.168.169.56', 6, NULL),
(5, 1, '57', '192.168.169.57', 6, NULL),
(6, 1, '58', '192.168.169.58', 6, NULL),
(7, 1, '59', '192.168.169.59', 6, NULL),
(8, 1, '60', '192.168.169.60', 6, NULL),
(9, 1, '66', '192.168.169.66', 6, NULL),
(10, 1, '67', '192.168.169.67', 6, NULL),
(11, 1, '68', '192.168.169.68', 6, NULL),
(12, 1, '69', '192.168.169.69', 6, NULL),
(13, 1, '70', '192.168.169.70', 6, NULL),
(14, 1, '71', '192.168.169.71', 6, NULL),
(15, 1, '72', '192.168.169.72', 6, NULL),
(16, 1, '73', '192.168.169.73', 6, NULL),
(17, 1, '74', '192.168.169.74', 6, NULL),
(18, 1, '75', '192.168.169.75', 6, NULL),
(19, 1, '76', '192.168.169.76', 6, NULL),
(20, 1, '82', '192.168.169.82', 6, NULL),
(21, 1, '83', '192.168.169.83', 6, NULL),
(22, 1, '84', '192.168.169.84', 6, NULL),
(23, 1, '85', '192.168.169.85', 6, NULL),
(24, 1, '86', '192.168.169.86', 6, NULL),
(25, 1, '87', '192.168.169.87', 6, NULL),
(26, 1, '88', '192.168.169.88', 6, NULL);

-- --------------------------------------------------------

--
-- Table structure for table `locations`
--

CREATE TABLE `locations` (
  `id` int(11) NOT NULL,
  `name` varchar(100) NOT NULL,
  `code` varchar(50) NOT NULL,
  `portal_name` varchar(100) NOT NULL,
  `portal_password` varchar(100) NOT NULL,
  `ip_schema` varchar(100) NOT NULL,
  `parameters` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`parameters`)),
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `locations`
--

INSERT INTO `locations` (`id`, `name`, `code`, `portal_name`, `portal_password`, `ip_schema`, `parameters`, `created_at`) VALUES
(1, 'Nad Al Sheba', 'NAD', 'nad_portal', 'nad_pass', '192.168.100.0/24', NULL, '2025-06-03 11:58:00');

-- --------------------------------------------------------

--
-- Table structure for table `manual_reviews`
--

CREATE TABLE `manual_reviews` (
  `id` int(11) NOT NULL,
  `camera_id` int(11) NOT NULL,
  `spot_number` int(11) NOT NULL,
  `event_time` datetime NOT NULL,
  `image_path` varchar(255) NOT NULL,
  `clip_path` varchar(255) DEFAULT NULL,
  `review_status` enum('PENDING','RESOLVED') NOT NULL DEFAULT 'PENDING',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

- --------------------------------------------------------

--
-- Table structure for table `plate_logs`
--

CREATE TABLE `plate_logs` (
  `id` int(11) NOT NULL,
  `camera_id` int(11) NOT NULL,
  `car_id` varchar(50) DEFAULT NULL,
  `plate_number` varchar(20) DEFAULT NULL,
  `plate_code` varchar(10) DEFAULT NULL,
  `plate_city` varchar(50) DEFAULT NULL,
  `confidence` int(11) DEFAULT NULL,
  `image_path` varchar(255) NOT NULL,
  `status` enum('READ','UNREAD') NOT NULL DEFAULT 'UNREAD',
  `attempt_ts` datetime NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `poles`
--

CREATE TABLE `poles` (
  `id` int(11) NOT NULL,
  `zone_id` int(11) NOT NULL,
  `code` varchar(50) NOT NULL,
  `location_id` int(11) NOT NULL,
  `number_of_cameras` int(11) DEFAULT 0,
  `server` varchar(100) DEFAULT NULL,
  `router` varchar(100) DEFAULT NULL,
  `router_ip` varchar(45) DEFAULT NULL,
  `router_vpn_ip` varchar(45) DEFAULT NULL,
  `location_coordinates` point DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `poles`
--

INSERT INTO `poles` (`id`, `zone_id`, `code`, `location_id`, `number_of_cameras`, `server`, `router`, `router_ip`, `router_vpn_ip`, `location_coordinates`) VALUES
(1, 1, 'P1', 1, 1, 'server1', 'router1', '192.168.100.10', '10.0.0.10', 0x000000000101000000e25817b7d110394004e78c28ed954b40),
(2, 1, 'P2', 1, 1, NULL, NULL, NULL, NULL, NULL);

-- --------------------------------------------------------

--
-- Table structure for table `reports`
--

CREATE TABLE `reports` (
  `id` int(11) NOT NULL,
  `camera_id` int(11) NOT NULL,
  `event` varchar(100) NOT NULL,
  `report_type` varchar(50) NOT NULL,
  `timestamp` datetime NOT NULL,
  `payload` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL CHECK (json_valid(`payload`)),
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

-- --------------------------------------------------------

--
-- Table structure for table `tickets`
--

CREATE TABLE `tickets` (
  `id` int(11) NOT NULL,
  `camera_id` int(11) NOT NULL,
  `spot_number` int(11) NOT NULL,
  `plate_number` varchar(20) NOT NULL,
  `plate_code` varchar(10) DEFAULT NULL,
  `plate_city` varchar(50) DEFAULT NULL,
  `confidence` int(11) DEFAULT NULL,
  `entry_time` datetime NOT NULL,
  `exit_time` datetime DEFAULT NULL,
  `parkonic_trip_id` int(11) DEFAULT NULL,
  `created_at` timestamp NOT NULL DEFAULT current_timestamp()
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `tickets`
-- --------------------------------------------------------

--
-- Table structure for table `users`
--

CREATE TABLE `users` (
  `id` int(11) NOT NULL,
  `username` varchar(50) NOT NULL,
  `password` varchar(100) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `users`
--

INSERT INTO `users` (`id`, `username`, `password`) VALUES
(1, 'testuser', 'testpass');

-- --------------------------------------------------------

--
-- Table structure for table `zones`
--

CREATE TABLE `zones` (
  `id` int(11) NOT NULL,
  `code` varchar(50) NOT NULL,
  `parameters` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin DEFAULT NULL CHECK (json_valid(`parameters`)),
  `location_id` int(11) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci;

--
-- Dumping data for table `zones`
--

INSERT INTO `zones` (`id`, `code`, `parameters`, `location_id`) VALUES
(1, 'Z1', NULL, 1);

--
-- Indexes for dumped tables
--

--
-- Indexes for table `cameras`
--
ALTER TABLE `cameras`
  ADD PRIMARY KEY (`id`),
  ADD KEY `pole_id` (`pole_id`);

--
-- Indexes for table `locations`
--
ALTER TABLE `locations`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `code` (`code`);

--
-- Indexes for table `manual_reviews`
--
ALTER TABLE `manual_reviews`
  ADD PRIMARY KEY (`id`),
  ADD KEY `camera_id` (`camera_id`);

--
-- Indexes for table `plate_logs`
--
ALTER TABLE `plate_logs`
  ADD PRIMARY KEY (`id`),
  ADD KEY `camera_id` (`camera_id`);

--
-- Indexes for table `poles`
--
ALTER TABLE `poles`
  ADD PRIMARY KEY (`id`),
  ADD KEY `zone_id` (`zone_id`),
  ADD KEY `location_id` (`location_id`);

--
-- Indexes for table `reports`
--
ALTER TABLE `reports`
  ADD PRIMARY KEY (`id`),
  ADD KEY `camera_id` (`camera_id`);

--
-- Indexes for table `tickets`
--
ALTER TABLE `tickets`
  ADD PRIMARY KEY (`id`),
  ADD KEY `camera_id` (`camera_id`);

--
-- Indexes for table `users`
--
ALTER TABLE `users`
  ADD PRIMARY KEY (`id`),
  ADD UNIQUE KEY `username` (`username`);

--
-- Indexes for table `zones`
--
ALTER TABLE `zones`
  ADD PRIMARY KEY (`id`),
  ADD KEY `location_id` (`location_id`);

--
-- AUTO_INCREMENT for dumped tables
--

--
-- AUTO_INCREMENT for table `cameras`
--
ALTER TABLE `cameras`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=28;

--
-- AUTO_INCREMENT for table `locations`
--
ALTER TABLE `locations`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=2;

--
-- AUTO_INCREMENT for table `manual_reviews`
--
ALTER TABLE `manual_reviews`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=8628;

--
-- AUTO_INCREMENT for table `plate_logs`
--
ALTER TABLE `plate_logs`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=9187;

--
-- AUTO_INCREMENT for table `poles`
--
ALTER TABLE `poles`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=3;

--
-- AUTO_INCREMENT for table `reports`
--
ALTER TABLE `reports`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT;

--
-- AUTO_INCREMENT for table `tickets`
--
ALTER TABLE `tickets`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=9141;

--
-- AUTO_INCREMENT for table `users`
--
ALTER TABLE `users`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=2;

--
-- AUTO_INCREMENT for table `zones`
--
ALTER TABLE `zones`
  MODIFY `id` int(11) NOT NULL AUTO_INCREMENT, AUTO_INCREMENT=2;

--
-- Constraints for dumped tables
--

--
-- Constraints for table `cameras`
--
ALTER TABLE `cameras`
  ADD CONSTRAINT `cameras_ibfk_1` FOREIGN KEY (`pole_id`) REFERENCES `poles` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `manual_reviews`
--
ALTER TABLE `manual_reviews`
  ADD CONSTRAINT `manual_reviews_ibfk_1` FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `plate_logs`
--
ALTER TABLE `plate_logs`
  ADD CONSTRAINT `plate_logs_ibfk_1` FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `poles`
--
ALTER TABLE `poles`
  ADD CONSTRAINT `poles_ibfk_1` FOREIGN KEY (`zone_id`) REFERENCES `zones` (`id`) ON DELETE CASCADE,
  ADD CONSTRAINT `poles_ibfk_2` FOREIGN KEY (`location_id`) REFERENCES `locations` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `reports`
--
ALTER TABLE `reports`
  ADD CONSTRAINT `reports_ibfk_2` FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`);

--
-- Constraints for table `tickets`
--
ALTER TABLE `tickets`
  ADD CONSTRAINT `tickets_ibfk_1` FOREIGN KEY (`camera_id`) REFERENCES `cameras` (`id`) ON DELETE CASCADE;

--
-- Constraints for table `zones`
--
ALTER TABLE `zones`
  ADD CONSTRAINT `zones_ibfk_1` FOREIGN KEY (`location_id`) REFERENCES `locations` (`id`) ON DELETE CASCADE;
COMMIT;

/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40101 SET CHARACTER_SET_RESULTS=@OLD_CHARACTER_SET_RESULTS */;
/*!40101 SET COLLATION_CONNECTION=@OLD_COLLATION_CONNECTION */;

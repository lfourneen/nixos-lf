{ pkgs, inputs, config, ... }:

{
  # Install package
  home.packages = with pkgs; [
    inputs.noctalia.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];

  # Configure options
  programs.noctalia-shell = {
    enable = true;
    settings = {
      settingsVersion = 57;
      # Bar Configuration
      bar = {
        barType = "framed";
        position = "top";
        monitors = [];
        density = "standard";
        showOutline = false;
        showCapsule = false;
        capsuleOpacity = 1;
        capsuleColorKey = "none";
        widgetSpacing = 6;
        contentPadding = 2;
        fontScale = 1;
        backgroundOpacity = 0.8;
        useSeparateOpacity = true;
        floating = false;
        marginVertical = 6;
        marginHorizontal = 4;
        frameThickness = 8;
        frameRadius = 12;
        outerCorners = true;
        hideOnOverview = false;
        displayMode = "always_visible";
        autoHideDelay = 500;
        autoShowDelay = 150;
        showOnWorkspaceSwitch = true;
        widgets = {
          left = [
            { colorizeDistroLogo = false; colorizeSystemIcon = "none"; customIconPath = ""; enableColorization = false; icon = "noctalia"; id = "ControlCenter"; useDistroLogo = true; }
            { id = "plugin:network-indicator"; }
            { id = "Network"; displayMode = "onhover"; iconColor = "none"; textColor = "none"; }
            { id = "plugin:port-monitor"; }
            { id = "plugin:mini-docker"; }
            { id = "Bluetooth"; displayMode = "onhover"; iconColor = "none"; textColor = "none"; }
            { id = "plugin:usb-drive-manager"; }
            { id = "plugin:linux-wallpaperengine-controller"; }
            { id = "plugin:screen-toolkit"; }
            { id = "plugin:color-scheme-creator"; }
            { blacklist = []; chevronColor = "none"; colorizeIcons = false; drawerEnabled = true; hidePassive = false; id = "Tray"; pinned = []; }
          ];
          center = [
            { characterCount = 2; colorizeIcons = false; emptyColor = "secondary"; enableScrollWheel = true; focusedColor = "primary"; followFocusedScreen = false; fontWeight = "bold"; groupedBorderOpacity = 1; hideUnoccupied = false; iconScale = 1; id = "Workspace"; labelMode = "none"; occupiedColor = "secondary"; pillSize = 0.6; showApplications = false; showBadge = true; showLabelsOnlyWhenOccupied = true; unfocusedIconsOpacity = 1; }
          ];
          right = [
            { id = "plugin:catwalk"; }
            { id = "plugin:privacy-indicator"; }
            { id = "plugin:assistant-panel"; }
            { id = "plugin:simple-notes"; }
            { id = "plugin:todo"; }
            { id = "plugin:clipper"; }
            { id = "plugin:timer"; }
            { clockColor = "none"; customFont = ""; formatHorizontal = "yyyy-MM-dd, HH:mm ddd"; formatVertical = "HH mm - dd MM"; id = "Clock"; tooltipFormat = "yyyy-MM-dd, HH:mm ddd"; useCustomFont = false; }
            #{ deviceNativePath = ""; displayMode = "onhover"; hideIfIdle = false; hideIfNotDetected = true; id = "Battery"; showNoctaliaPerformance = false; showPowerProfiles = false; }
            { id = "plugin:battery-monitor-plus"; }
          ];
        };
        mouseWheelAction = "none";
        reverseScroll = false;
        mouseWheelWrap = true;
        middleClickAction = "none";
        middleClickFollowMouse = false;
        middleClickCommand = "";
        rightClickAction = "controlCenter";
        rightClickFollowMouse = true;
        rightClickCommand = "";
        screenOverrides = [];
      };

      # General Settings
      general = {
        avatarImage = "${config.home.homeDirectory}/.face";
        dimmerOpacity = 0.2;
        showScreenCorners = false;
        forceBlackScreenCorners = false;
        scaleRatio = 1;
        radiusRatio = 0.2;
        iRadiusRatio = 1;
        boxRadiusRatio = 1;
        screenRadiusRatio = 1;
        animationSpeed = 1;
        animationDisabled = false;
        compactLockScreen = false;
        lockScreenAnimations = true;
        lockOnSuspend = true;
        showSessionButtonsOnLockScreen = true;
        showHibernateOnLockScreen = false;
        enableLockScreenMediaControls = false;
        enableShadows = true;
        enableBlurBehind = true;
        shadowDirection = "bottom_right";
        shadowOffsetX = 2;
        shadowOffsetY = 3;
        language = "";
        allowPanelsOnScreenWithoutBar = true;
        showChangelogOnStartup = true;
        telemetryEnabled = true;
        enableLockScreenCountdown = true;
        lockScreenCountdownDuration = 30000;
        autoStartAuth = false;
        allowPasswordWithFprintd = false;
        clockStyle = "custom";
        clockFormat = "hh\\nmm";
        passwordChars = true;
        lockScreenMonitors = [];
        lockScreenBlur = 0.5;
        lockScreenTint = 0.5;
        keybinds = {
          keyUp = [ "Up" ];
          keyDown = [ "Down" ];
          keyLeft = [ "Left" ];
          keyRight = [ "Right" ];
          keyEnter = [ "Return" "Enter" ];
          keyEscape = [ "Esc" ];
          keyRemove = [ "Del" ];
        };
        reverseScroll = false;
      };

      # UI Settings
      ui = {
        fontDefault = "Maple Mono NF CN";
        fontFixed = "Maple Mono NF CN";
        fontDefaultScale = 1;
        fontFixedScale = 1;
        tooltipsEnabled = true;
        boxBorderEnabled = false;
        panelBackgroundOpacity = 0.8;
        panelsAttachedToBar = true;
        settingsPanelMode = "attached";
        settingsPanelSideBarCardStyle = false;
      };

      # Location & Weather
      location = {
        name = "Chengdu, China";
        weatherEnabled = true;
        weatherShowEffects = true;
        useFahrenheit = false;
        use12hourFormat = false;
        showWeekNumberInCalendar = false;
        showCalendarEvents = true;
        showCalendarWeather = true;
        analogClockInCalendar = false;
        firstDayOfWeek = -1;
        hideWeatherTimezone = false;
        hideWeatherCityName = false;
        autoLocate = false;
      };

      # Calendar Cards
      calendar = {
        cards = [
          { enabled = true; id = "calendar-header-card"; }
          { enabled = true; id = "calendar-month-card"; }
          { enabled = true; id = "weather-card"; }
        ];
      };

      # Wallpaper Configuration
      wallpaper = {
        enabled = true;
        overviewEnabled = false;
        directory = "${config.home.homeDirectory}/Pictures/Wallpapers";
        monitorDirectories = [];
        enableMultiMonitorDirectories = false;
        showHiddenFiles = false;
        viewMode = "single";
        setWallpaperOnAllMonitors = true;
        fillMode = "crop";
        fillColor = "#000000";
        useSolidColor = false;
        solidColor = "#1a1a2e";
        automationEnabled = false;
        wallpaperChangeMode = "random";
        randomIntervalSec = 300;
        transitionDuration = 1500;
        transitionType = "random";
        skipStartupTransition = false;
        transitionEdgeSmoothness = 0.05;
        panelPosition = "follow_bar";
        hideWallpaperFilenames = false;
        overviewBlur = 0.4;
        overviewTint = 0.6;
        useWallhaven = false;
        wallhavenQuery = "";
        wallhavenSorting = "relevance";
        wallhavenOrder = "desc";
        wallhavenCategories = "111";
        wallhavenPurity = "100";
        wallhavenRatios = "";
        wallhavenApiKey = "";
        wallhavenResolutionMode = "atleast";
        wallhavenResolutionWidth = "";
        wallhavenResolutionHeight = "";
        sortOrder = "name";
        favorites = [];
      };

      # App Launcher
      appLauncher = {
        enableClipboardHistory = false;
        autoPasteClipboard = false;
        enableClipPreview = true;
        clipboardWrapText = true;
        clipboardWatchTextCommand = "wl-paste --type text --watch cliphist store";
        clipboardWatchImageCommand = "wl-paste --type image --watch cliphist store";
        position = "center";
        pinnedApps = [];
        useApp2Unit = false;
        sortByMostUsed = true;
        terminalCommand = "xterm -e";
        customLaunchPrefixEnabled = false;
        customLaunchPrefix = "";
        viewMode = "list";
        showCategories = true;
        iconMode = "tabler";
        showIconBackground = false;
        enableSettingsSearch = true;
        enableWindowsSearch = true;
        enableSessionSearch = true;
        ignoreMouseInput = false;
        screenshotAnnotationTool = "";
        overviewLayer = false;
        density = "default";
      };

      # Control Center
      controlCenter = {
        position = "close_to_bar_button";
        diskPath = "/";
        shortcuts = {
          left = [
            { id = "Network"; }
            { id = "Bluetooth"; }
            { id = "WallpaperSelector"; }
            { id = "NoctaliaPerformance"; }
          ];
          right = [
            { id = "Notifications"; }
            { id = "PowerProfile"; }
            { id = "KeepAwake"; }
            { id = "NightLight"; }
          ];
        };
        cards = [
          { enabled = true; id = "profile-card"; }
          { enabled = true; id = "shortcuts-card"; }
          { enabled = true; id = "audio-card"; }
          { enabled = true; id = "brightness-card"; }
          { enabled = true; id = "weather-card"; }
          { enabled = true; id = "media-sysmon-card"; }
        ];
      };

      # System Monitor
      systemMonitor = {
        cpuWarningThreshold = 80;
        cpuCriticalThreshold = 90;
        tempWarningThreshold = 80;
        tempCriticalThreshold = 90;
        gpuWarningThreshold = 80;
        gpuCriticalThreshold = 90;
        memWarningThreshold = 80;
        memCriticalThreshold = 90;
        swapWarningThreshold = 80;
        swapCriticalThreshold = 90;
        diskWarningThreshold = 80;
        diskCriticalThreshold = 90;
        diskAvailWarningThreshold = 20;
        diskAvailCriticalThreshold = 10;
        batteryWarningThreshold = 20;
        batteryCriticalThreshold = 5;
        enableDgpuMonitoring = false;
        useCustomColors = false;
        warningColor = "";
        criticalColor = "";
        externalMonitor = "resources || missioncenter || jdsystemmonitor || corestats || system-monitoring-center || gnome-system-monitor || plasma-systemmonitor || mate-system-monitor || ukui-system-monitor || deepin-system-monitor || pantheon-system-monitor";
      };

      # Performance Options
      noctaliaPerformance = {
        disableWallpaper = true;
        disableDesktopWidgets = true;
      };

      # Dock Settings
      dock = {
        enabled = true;
        position = "bottom";
        displayMode = "auto_hide";
        dockType = "floating";
        backgroundOpacity = 1;
        floatingRatio = 1;
        size = 1.5;
        onlySameOutput = true;
        monitors = [ "eDP-1" "HDMI-A-1" ];
        pinnedApps = [];
        colorizeIcons = false;
        showLauncherIcon = true;
        launcherPosition = "start";
        launcherUseDistroLogo = true;
        launcherIcon = "";
        launcherIconColor = "none";
        pinnedStatic = true;
        inactiveIndicators = true;
        groupApps = false;
        groupContextMenuMode = "extended";
        groupClickAction = "cycle";
        groupIndicatorStyle = "dots";
        deadOpacity = 0.6;
        animationSpeed = 1;
        sitOnFrame = false;
        showDockIndicator = true;
        indicatorThickness = 6;
        indicatorColor = "primary";
        indicatorOpacity = 0.5;
      };

      # Network Configuration
      network = {
        wifiEnabled = true;
        airplaneModeEnabled = false;
        bluetoothRssiPollingEnabled = false;
        bluetoothRssiPollIntervalMs = 10000;
        networkPanelView = "wifi";
        wifiDetailsViewMode = "grid";
        bluetoothDetailsViewMode = "grid";
        bluetoothHideUnnamedDevices = false;
        disableDiscoverability = false;
        bluetoothAutoConnect = true;
      };

      # Session Menu
      sessionMenu = {
        enableCountdown = true;
        countdownDuration = 10000;
        position = "center";
        showHeader = true;
        showKeybinds = true;
        largeButtonsStyle = false;
        largeButtonsLayout = "grid";
        powerOptions = [
          { action = "lock"; command = ""; countdownEnabled = true; enabled = true; keybind = "1"; }
          { action = "suspend"; command = ""; countdownEnabled = true; enabled = true; keybind = "2"; }
          { action = "hibernate"; command = ""; countdownEnabled = true; enabled = true; keybind = "3"; }
          { action = "reboot"; command = ""; countdownEnabled = true; enabled = true; keybind = "4"; }
          { action = "logout"; command = ""; countdownEnabled = true; enabled = true; keybind = "5"; }
          { action = "shutdown"; command = ""; countdownEnabled = true; enabled = true; keybind = "6"; }
          { action = "userspaceReboot"; command = ""; countdownEnabled = true; enabled = false; keybind = ""; }
          { action = "rebootToUefi"; command = ""; countdownEnabled = true; enabled = true; keybind = "7"; }
        ];
      };

      # Notifications
      notifications = {
        enabled = true;
        enableMarkdown = false;
        density = "default";
        monitors = [];
        location = "top_right";
        overlayLayer = true;
        backgroundOpacity = 1;
        respectExpireTimeout = false;
        lowUrgencyDuration = 3;
        normalUrgencyDuration = 8;
        criticalUrgencyDuration = 15;
        clearDismissed = true;
        saveToHistory = {
          low = true;
          normal = true;
          critical = true;
        };
        sounds = {
          enabled = false;
          volume = 0.5;
          separateSounds = false;
          criticalSoundFile = "";
          normalSoundFile = "";
          lowSoundFile = "";
          excludedApps = "discord,firefox,chrome,chromium,edge";
        };
        enableMediaToast = false;
        enableKeyboardLayoutToast = true;
        enableBatteryToast = true;
      };

      # OSD Settings
      osd = {
        enabled = true;
        location = "top_right";
        autoHideMs = 2000;
        overlayLayer = true;
        backgroundOpacity = 1;
        enabledTypes = [ 0 1 2 ];
        monitors = [ "eDP-1" ];
      };

      # Audio Settings
      audio = {
        volumeStep = 5;
        volumeOverdrive = false;
        spectrumFrameRate = 30;
        visualizerType = "linear";
        mprisBlacklist = [];
        preferredPlayer = "";
        volumeFeedback = false;
        volumeFeedbackSoundFile = "";
      };

      # Brightness Settings
      brightness = {
        brightnessStep = 5;
        enforceMinimum = true;
        enableDdcSupport = false;
        backlightDeviceMappings = [];
      };

      # Color Schemes
      colorSchemes = {
        useWallpaperColors = true;
        predefinedScheme = "Ayu";
        darkMode = true;
        schedulingMode = "off";
        manualSunrise = "06:30";
        manualSunset = "18:30";
        generationMethod = "tonal-spot";
        monitorForColors = "";
      };

      # Templates
      templates = {
        activeTemplates = [];
        enableUserTheming = false;
      };

      # Night Light
      nightLight = {
        enabled = false;
        forced = false;
        autoSchedule = true;
        nightTemp = "4000";
        dayTemp = "6500";
        manualSunrise = "06:30";
        manualSunset = "18:30";
      };

      # Hooks
      hooks = {
        enabled = false;
        wallpaperChange = "";
        darkModeChange = "";
        screenLock = "";
        screenUnlock = "";
        performanceModeEnabled = "";
        performanceModeDisabled = "";
        startup = "";
        session = "";
      };

      # Plugin Internal Settings
      plugins = {
        autoUpdate = true;
      };

      # Idle Management
      idle = {
        enabled = true;
        screenOffTimeout = 3600;
        lockTimeout = 3900;
        suspendTimeout = 0;
        fadeDuration = 5;
        screenOffCommand = "";
        lockCommand = "";
        suspendCommand = "";
        resumeScreenOffCommand = "";
        resumeLockCommand = "";
        resumeSuspendCommand = "";
        customCommands = "[]";
      };

      # Desktop Widgets
      desktopWidgets = {
        enabled = false;
        overviewEnabled = true;
        gridSnap = false;
        monitorWidgets = [];
      };
    };

    # Plugin Configuration
    plugins = {
      sources = [
        {
          enabled = true;
          name = "Official Noctalia Plugins";
          url = "https://github.com/noctalia-dev/noctalia-plugins";
        }
      ];
      states = {
        catwalk = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        network-indicator = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        timer = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        simple-notes = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        todo = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        clipper = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        weather-indicator = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        assistant-panel = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        screen-toolkit = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        color-scheme-creator = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        usb-drive-manager = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        ip-monitor = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        privacy-indicator = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        port-monitor = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        air-quality = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        linux-wallpaperengine-controller = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        latency-monitor = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        mini-docker = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
        battery-monitor-plus = {
          enabled = true;
          sourceUrl = "https://github.com/noctalia-dev/noctalia-plugins";
        };
      };
      version = 1;
    };

    pluginSettings = {
      catwalk = {
        minimumThreshold = 25;
        hideBackground = true;
      };
    };
  };

  # State File
  home.file.".cache/noctalia/wallpapers.json" = {
    text = builtins.toJSON {
      defaultWallpaper = "${../../wallpapers/Kafuu-Chino.png}";
      wallpapers = {
        "eDP-1" = "${../../wallpapers/Kafuu-Chino.png}";
	"HDMI-A-1" = "${../../wallpapers/cocoa.png}";
      };
    };
  };
}


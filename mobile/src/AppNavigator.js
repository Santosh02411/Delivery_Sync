import React from "react";
import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { TouchableOpacity, Text } from "react-native";
import DeliveryListScreen from "./screens/DeliveryListScreen";
import DeliveryDetailScreen from "./screens/DeliveryDetailScreen";
import SettingsScreen from "./screens/SettingsScreen";
import { colors } from "./theme";

const Stack = createNativeStackNavigator();

const navTheme = {
  ...DefaultTheme,
  dark: true,
  colors: {
    ...DefaultTheme.colors,
    background: colors.bgPage,
    card: colors.bgSurface,
    text: colors.textPrimary,
    border: colors.border,
    primary: colors.accent,
  },
};

export default function AppNavigator() {
  return (
    <NavigationContainer theme={navTheme}>
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.bgSurface },
          headerTintColor: colors.textPrimary,
          headerTitleStyle: { fontWeight: "700" },
        }}
      >
        <Stack.Screen
          name="DeliveryList"
          component={DeliveryListScreen}
          options={({ navigation }) => ({
            title: "My Deliveries",
            headerRight: () => (
              <TouchableOpacity onPress={() => navigation.navigate("Settings")}>
                <Text style={{ color: colors.accent, fontWeight: "600" }}>Settings</Text>
              </TouchableOpacity>
            ),
          })}
        />
        <Stack.Screen name="DeliveryDetail" component={DeliveryDetailScreen} options={{ title: "Delivery" }} />
        <Stack.Screen name="Settings" component={SettingsScreen} options={{ title: "Settings" }} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}
